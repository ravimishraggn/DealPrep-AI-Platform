"""Dev helper: generate a sample PDF with prose + a table for pipeline testing.

    python examples/make_sample_pdf.py examples/sample_deal.pdf

The PDF contains a paragraph of prose (→ text documents) and a ruled comps table
(→ structured records), exercising the PdfExtractor "one record → both" path.
"""
from __future__ import annotations

import sys

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


def build(path: str) -> None:
    """Write a sample deal PDF (prose + comps table) to ``path``."""
    doc = SimpleDocTemplate(path, pagesize=letter)
    styles = getSampleStyleSheet()
    prose = (
        "Acme Corp Valuation Memo. Acme Corp is being compared against Globex and "
        "Initech. Acme's reported EBITDA includes revenue from a related entity owned "
        "by the same sponsor, Falcon Capital. Once normalized, Acme's EV/EBITDA "
        "multiple aligns with its peers. Jane Smith sits on the board of both Acme "
        "Corp and Globex."
    )
    table_data = [
        ["Company", "EV/EBITDA", "Sponsor"],
        ["Acme Corp", "13.5", "Falcon Capital"],
        ["Globex", "9.8", "Falcon Capital"],
        ["Initech", "11.2", "Bluewater"],
    ]
    table = Table(table_data)
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 1, colors.black),
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
            ]
        )
    )
    doc.build([Paragraph(prose, styles["BodyText"]), Spacer(1, 18), table])


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "examples/sample_deal.pdf"
    build(out)
    print(f"wrote {out}")
