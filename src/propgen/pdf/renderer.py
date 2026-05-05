"""ReportLab PDF renderer — programmatic proposal layout."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Image,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from propgen._time import format_local
from propgen.config.loader import PropGenConfig
from propgen.models import LineItem, Proposal, ProposalVersion


def _simple_md_paragraphs(text: str, style: ParagraphStyle) -> list[Any]:
    """Headings (#), bullets (-), and **bold** → ReportLab Paragraphs."""
    flowables: list[Any] = []
    for block in re.split(r"\n{2,}", text.strip() or ""):
        lines = [ln.strip() for ln in block.split("\n") if ln.strip()]
        if not lines:
            continue
        if lines[0].startswith("# "):
            raw = escape_md(lines[0][2:])
            flowables.append(Paragraph(f"<b>{raw}</b>", style))
            for ln in lines[1:]:
                flowables.append(Paragraph(_inline_md(ln), style))
        elif lines[0].startswith("## "):
            raw = escape_md(lines[0][3:])
            flowables.append(Paragraph(f"<b>{raw}</b>", style))
            for ln in lines[1:]:
                flowables.append(Paragraph(_inline_md(ln), style))
        else:
            for ln in lines:
                if ln.startswith("- "):
                    flowables.append(Paragraph("• " + _inline_md(ln[2:]), style))
                else:
                    flowables.append(Paragraph(_inline_md(ln), style))
    return flowables


def escape_md(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _inline_md(s: str) -> str:
    x = escape_md(s)
    x = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", x)
    x = re.sub(r"\*(.+?)\*", r"<i>\1</i>", x)
    return x


def _split_narrative(narrative_md: str) -> tuple[str, str]:
    m = re.search(r"\n(?=#+\s)", narrative_md)
    if m:
        return narrative_md[: m.start()].strip(), narrative_md[m.start() :].strip()
    return narrative_md.strip(), ""


def _sync_render(
    proposal: Proposal,
    version: ProposalVersion,
    config: PropGenConfig,
    out_path: Path,
    sign_anchor: str,
) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    styles = getSampleStyleSheet()
    body = ParagraphStyle(
        name="Body",
        parent=styles["Normal"],
        fontSize=10,
        leading=14,
        alignment=TA_LEFT,
        spaceAfter=6,
    )

    def on_page(canv, doc) -> None:  # noqa: ANN001
        canv.saveState()
        brand = config.business.brand
        footer = brand.footer_text or brand.legal_name or config.business.name
        canv.setFont("Helvetica", 8)
        canv.drawString(inch * 0.75, 0.55 * inch, footer)
        canv.drawRightString(
            letter[0] - inch * 0.75,
            0.55 * inch,
            f"Page {doc.page}",
        )
        canv.restoreState()

    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=letter,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.9 * inch,
        bottomMargin=0.85 * inch,
    )
    story: list[Any] = []
    header_tbl_data: list[list[Any]] = []
    logo_path = Path(config.business.brand.logo_path)
    if logo_path.is_file():
        try:
            im = Image(str(logo_path), width=1.4 * inch, height=0.45 * inch)
            hdr = Paragraph(
                f"<b>{config.business.name}</b><br/>"
                f"{config.operator_email}<br/>"
                f"{config.business.address or ''}",
                ParagraphStyle(
                    name="hdr",
                    parent=body,
                    alignment=TA_RIGHT,
                    fontSize=9,
                ),
            )
            header_tbl_data.append([im, hdr])
        except OSError:
            pass
    if header_tbl_data:
        t = Table(header_tbl_data, colWidths=[2.2 * inch, 4.0 * inch])
        t.setStyle(
            TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("ALIGN", (1, 0), (1, 0), "RIGHT")])
        )
        story.append(t)
        story.append(Spacer(1, 14))

    story.append(Paragraph("<b>PROPOSAL</b>", body))
    prep = format_local(proposal.created_at, config.business.timezone)
    valid = (
        format_local(proposal.expires_at, config.business.timezone) if proposal.expires_at else ""
    )
    meta = Table(
        [
            ["Proposal ID:", proposal.id[:8]],
            ["Prepared for:", proposal.client.name or proposal.client.email],
            ["Prepared on:", prep],
            ["Valid until:", valid or "—"],
        ],
        colWidths=[1.2 * inch, 5 * inch],
    )
    meta.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 9), ("TOPPADDING", (0, 0), (-1, -1), 3)]))
    story.append(meta)
    story.append(Spacer(1, 12))

    cover, body_md = _split_narrative(version.narrative_md)
    story.extend(_simple_md_paragraphs(cover or " ", body))
    if body_md:
        story.append(Spacer(1, 10))
        story.extend(_simple_md_paragraphs(body_md, body))

    story.append(Spacer(1, 14))
    story.append(Paragraph("<b>Investment</b>", body))
    li_rows: list[list[str]] = [
        ["Qty", "Description", "Unit", "Unit price", "Line total"],
    ]
    for li in sorted(version.line_items, key=lambda x: x.sort_order):
        li_rows.append(
            [
                f"{li.quantity:g}",
                f"{li.name}<br/><i>{li.description}</i>" if li.description else li.name,
                li.unit,
                f"{li.unit_price:.2f}",
                f"{li.line_total:.2f}",
            ]
        )
    li_rows.append(["", "", "", "Subtotal", f"{proposal.subtotal:.2f}"])
    li_rows.append(["", "", "", "Tax", f"{proposal.tax_amount:.2f}"])
    li_rows.append(["", "", "", "Discount", f"-{proposal.discount_amount:.2f}"])
    li_rows.append(["", "", "", "<b>Total</b>", f"<b>{proposal.total:.2f} {proposal.currency}</b>"])
    tbl = Table(li_rows, colWidths=[0.5 * inch, 3.0 * inch, 0.7 * inch, 0.9 * inch, 1.0 * inch])
    tbl.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f0f0f0")),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    story.append(tbl)

    story.append(PageBreak())
    story.extend(_simple_md_paragraphs(config.proposal.accept_terms_md, body))
    story.append(Spacer(1, 24))
    story.append(Paragraph(f"<b>Signature</b><br/>{sign_anchor}", body))
    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    return out_path


async def render_proposal_pdf(
    proposal: Proposal,
    version: ProposalVersion,
    config: PropGenConfig,
    out_path: Path,
) -> Path:
    anchor = config.docusign.sign_here_anchor or "<<SIGN_HERE>>"
    return await asyncio.to_thread(_sync_render, proposal, version, config, out_path, anchor)


__all__ = ["render_proposal_pdf"]
