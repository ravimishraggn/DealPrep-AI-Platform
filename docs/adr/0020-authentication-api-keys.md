# ADR 0020 — Authentication & API Key Management

| Field | Value |
|---|---|
| **Status** | Proposed |
| **Date** | 2026-06-23 |
| **Deciders** | Platform Engineering, Security |
| **Phase** | 9 — Production Readiness |
| **PRD reference** | §7 "API Gateway — Auth, routing, request validation — single front door" |
| **Relates to** | ADR 0015 (guardrails + audit log), ADR 0019 (observability) |

---

## Context

The platform has **no authentication at all**.  Every API endpoint is open.  Anyone who can
reach the server can:

- Create tenants, register sources, trigger ingestion runs
- Read any tenant's analysis history, risk signals, and document contents via `/inspect`
- Call `/analyze` and consume LLM budget belonging to another tenant
- Enumerate all tenants via the capabilities endpoint

This is acceptable for local development.  It is a blocker for:

- Handing credentials to a PE analyst to use in a real deal room
- Running multiple tenants on a shared server (one tenant could read another's data)
- Passing any enterprise security review

The PRD describes an API Gateway as "single front door" for auth and routing.  The long-term
vision is a .NET API Gateway.  This ADR covers the **Python-layer auth** that ships before
that .NET tier exists — and that the .NET tier will delegate to for token validation.

### What we are NOT building in this ADR

- SSO / SAML / OAuth2 identity provider (Phase 10 — too complex for current team size)
- Role-based access control UI (Phase 10)
- Per-endpoint permission matrix (Phase 10)
- .NET API Gateway (future)

### What we ARE building

A lightweight **API key + JWT** auth layer that:

1. Issues per-tenant API keys (long-lived, for machine clients)
2. Issues short-lived JWTs (for browser/dashboard sessions)
3. Enforces tenant-scoping — a key for tenant T-001 can only access T-001's data
4. Provides a minimal admin key for platform-level operations (create tenant, etc.)

---

## Decision

Implement **two authentication modes** on the same FastAPI middleware:

| Mode | Who uses it | Token type | Lifetime |
|---|---|---|---|
| API Key | Connector integrations, CI pipelines, external scripts | `Bearer dp_live_<32-char-hex>` | Permanent (until revoked) |
| Session JWT | Analysts using the dashboard UI | `Bearer eyJ...` | 1 hour (auto-refreshed) |
| Admin Key | Platform operations only | `Bearer dp_admin_<32-char-hex>` | Permanent (until revoked) |

Both modes go through the same FastAPI `Depends(get_current_tenant)` dependency.  Routes see
only the resolved `tenant_id` — they do not know whether the caller used a key or JWT.

---

## API Key Design

### Key format

```
dp_live_a3f8b2c1d4e5f6789012345678901234
│       │
│       └─ 32 hex chars (128 bits of entropy) — generated with secrets.token_hex(16)
└─── prefix identifies environment and key type (easy to scan in logs/code)
```

Environment prefixes:

| Prefix | Environment |
|---|---|
| `dp_live_` | Production |
| `dp_test_` | Staging / CI |
| `dp_admin_` | Platform admin operations |

Scanning for accidentally committed keys: a GitHub secret scanning pattern can match
`dp_(live|test|admin)_[0-9a-f]{32}` — register this pattern when the repo becomes private.

### Key storage

Never store the raw key.  Store a **SHA-256 hash** of the key in Postgres:

```python
import hashlib, secrets

def generate_api_key(prefix: str = "dp_live") -> tuple[str, str]:
    """Returns (raw_key, stored_hash). raw_key shown to user once; stored_hash persisted."""
    raw = f"{prefix}_{secrets.token_hex(16)}"
    hashed = hashlib.sha256(raw.encode()).hexdigest()
    return raw, hashed
```

New ORM table: `api_keys`

```python
class ApiKey(Base):
    __tablename__ = "api_keys"

    id:         Mapped[int]      = mapped_column(Integer, primary_key=True)
    key_hash:   Mapped[str]      = mapped_column(String(64), unique=True, index=True)
    key_prefix: Mapped[str]      = mapped_column(String(8))     # "dp_live_" etc.
    tenant_id:  Mapped[str|None] = mapped_column(String(36), nullable=True)  # null = admin key
    label:      Mapped[str]      = mapped_column(String(100))   # "Analyst laptop", "CI pipeline"
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_used:  Mapped[datetime|None] = mapped_column(nullable=True)
    revoked:    Mapped[bool]     = mapped_column(default=False)
    revoked_at: Mapped[datetime|None] = mapped_column(nullable=True)
    created_by: Mapped[str]      = mapped_column(String(100))   # who created it
```

### Key lookup (authentication hot path)

```python
async def verify_api_key(raw_key: str, db: Session) -> ApiKey:
    hashed = hashlib.sha256(raw_key.encode()).hexdigest()
    key = db.query(ApiKey).filter(
        ApiKey.key_hash == hashed,
        ApiKey.revoked == False
    ).first()
    if key is None:
        raise HTTPException(status_code=401, detail="Invalid or revoked API key")
    # Update last_used asynchronously (don't block the request)
    asyncio.create_task(_update_last_used(key.id))
    return key
```

Key lookup is a single indexed read — the `key_hash` index makes it O(log n).  No caching
needed for Phase 9 (< 1000 active keys in a PE firm).

---

## Session JWT Design (for the Dashboard)

When an analyst logs in via the dashboard, the server issues a short-lived JWT:

```python
import jwt  # PyJWT

JWT_SECRET = settings.jwt_secret      # 32-byte random secret from env
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_MINUTES = 60

def create_session_jwt(tenant_id: str, analyst_email: str) -> str:
    payload = {
        "sub": analyst_email,
        "tenant_id": tenant_id,
        "exp": datetime.utcnow() + timedelta(minutes=JWT_EXPIRE_MINUTES),
        "iat": datetime.utcnow(),
        "jti": str(uuid.uuid4()),      # unique JWT ID for revocation
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

def decode_session_jwt(token: str) -> dict:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Session expired — please log in again")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid session token")
```

**Login flow (simple — no OAuth for Phase 9):**

```
POST /auth/login
{ "tenant_id": "T-001", "api_key": "dp_live_a3f8b2c1..." }
→ verifies the API key → issues a 1-hour JWT → set as HttpOnly cookie

GET /ui/tenants/T-001/analyze
→ reads JWT from cookie → validates → proceeds
```

The dashboard always uses the cookie.  External API clients always use `Authorization: Bearer dp_live_...`.

---

## FastAPI Auth Middleware

```python
# app/auth.py

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

security = HTTPBearer(auto_error=False)

async def get_current_tenant(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: Session = Depends(get_session),
) -> str:
    """Resolve the current tenant_id from either an API key or a session JWT.

    Returns tenant_id if authenticated, raises HTTP 401 otherwise.
    Admin keys return None as tenant_id (platform-wide access).
    """
    # 1. Try Bearer token from Authorization header
    if credentials:
        token = credentials.credentials

        if token.startswith("dp_admin_"):
            return _verify_admin_key(token, db)

        if token.startswith("dp_live_") or token.startswith("dp_test_"):
            key = await verify_api_key(token, db)
            return key.tenant_id

        # Assume JWT
        payload = decode_session_jwt(token)
        return payload["tenant_id"]

    # 2. Try session cookie (dashboard)
    session_cookie = request.cookies.get("dealprep_session")
    if session_cookie:
        payload = decode_session_jwt(session_cookie)
        return payload["tenant_id"]

    raise HTTPException(status_code=401, detail="Authentication required")


def require_tenant_match(tenant_id_from_path: str):
    """Dependency: assert the authenticated tenant matches the path parameter."""
    async def check(authenticated_tenant: str = Depends(get_current_tenant)):
        if authenticated_tenant is not None and authenticated_tenant != tenant_id_from_path:
            raise HTTPException(
                status_code=403,
                detail=f"API key is scoped to a different tenant"
            )
        return authenticated_tenant
    return check
```

Usage in a router:

```python
# Before auth (Phase 1-8)
@router.post("/analyze")
async def analyze(tenant_id: str, payload: AnalyzeRequest, db=Depends(get_session)):
    ...

# After auth (Phase 9)
@router.post("/analyze")
async def analyze(
    tenant_id: str,
    payload: AnalyzeRequest,
    db: Session = Depends(get_session),
    _: str = Depends(require_tenant_match(tenant_id)),   # auth check
):
    ...
```

---

## API Endpoints

### Key management (admin only)

```
POST   /auth/keys              → create a new API key for a tenant
GET    /auth/keys              → list keys (hashed, with labels + last_used)
DELETE /auth/keys/{id}         → revoke a key immediately
POST   /auth/login             → exchange API key for session JWT (dashboard login)
POST   /auth/logout            → clear session cookie
```

### Key creation response

```json
{
  "id": 42,
  "key": "dp_live_a3f8b2c1d4e5f6789012345678901234",
  "label": "Analyst laptop — Jane Smith",
  "tenant_id": "T-001",
  "warning": "This key will never be shown again. Store it securely."
}
```

---

## Open Endpoints (no auth)

A small allowlist remains unauthenticated so the platform stays usable without credentials
in local dev:

```python
OPEN_PATHS = {
    "/health",
    "/openapi.json",
    "/docs",
    "/redoc",
    "/static",
    "/auth/login",
}
```

All other paths require authentication.  Controlled by a `settings.auth_enabled: bool = True`
flag (default False in local dev, True in production).

---

## Tenant Isolation with Auth

The combination of auth + Postgres RLS (ADR 0015 §governance) creates **two independent
enforcement layers**:

```
Request with dp_live_T001_key
    │
    ▼
FastAPI auth middleware:
  decoded tenant_id = "T-001"

    │
    ▼
Router param check:
  path tenant_id == "T-001" ✓

    │
    ▼
DB session: SET LOCAL app.tenant_id = 'T-001'

    │
    ▼
Postgres RLS:
  POLICY: tenant_id = current_setting('app.tenant_id')
  Any query missing WHERE tenant_id='T-001' is silently filtered
```

A single bug cannot expose cross-tenant data — both the application layer and the database
layer must fail simultaneously.

---

## File Plan

| File | Purpose |
|---|---|
| `app/auth.py` | `get_current_tenant`, `require_tenant_match`, JWT encode/decode, API key verify |
| `app/models.py` | Add `ApiKey` ORM model |
| `app/routers/auth.py` | `POST /auth/keys`, `GET /auth/keys`, `DELETE /auth/keys/{id}`, `POST /auth/login`, `POST /auth/logout` |
| `app/config.py` | Add `auth_enabled: bool`, `jwt_secret: str`, `jwt_expire_minutes: int` |
| `requirements.txt` | Add: `PyJWT>=2.8` |

---

## Migration Strategy (Existing Deployments)

Auth is added behind `settings.auth_enabled = False` by default.  Existing local dev
workflows continue without any changes.

To enable in production:
1. Set `DEALPREP_AUTH_ENABLED=true` in environment.
2. Set `DEALPREP_JWT_SECRET=<32-char random string>` in environment.
3. Run `POST /auth/keys` with an admin key to create the first tenant API keys.
4. Distribute keys to analysts.

---

## Consequences

**Positive:**
- Closes the most critical security gap for production deployment.
- Tenant isolation becomes double-enforced (application + database layer).
- API key rotation is self-service — revoke old key, issue new one, no downtime.
- The admin key pattern allows CI pipelines and connectors to authenticate without a user
  session.

**Negative / Risks:**
- Every existing route that reads `tenant_id` from the path must add the auth dependency.
  This is a mechanical but wide change (~8 routers × ~3 endpoints each = ~24 files touched).
- JWT secret must be set in production — if it is not set, the platform will refuse to start
  with `auth_enabled=True` (fail-fast, not silently insecure).
- No refresh token in Phase 9 — analysts must re-login after 1 hour of inactivity.  Phase 10
  can add refresh tokens when SSO is added.
