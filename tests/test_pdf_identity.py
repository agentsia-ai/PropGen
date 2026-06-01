"""PDF identity — operator author on document, not agent persona."""

from __future__ import annotations

from pathlib import Path

from pypdf import PdfReader

from propgen.pdf.renderer import _sync_render


def _pdf_text(path: Path) -> str:
    reader = PdfReader(str(path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def test_pdf_renders_operator_name_not_agent_name(
    test_config,
    sample_proposal,
    sample_version,
    tmp_path: Path,
) -> None:
    out = tmp_path / "proposal.pdf"
    anchor = "<<SIGN_HERE>>"
    _sync_render(sample_proposal, sample_version, test_config, out, anchor)
    text = _pdf_text(out)
    assert "Pat Operator" in text
    assert "Principal Consultant" in text
    assert "Proposal Assistant" not in text
