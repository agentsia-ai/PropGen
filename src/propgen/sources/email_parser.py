"""Parse proposal intent from plain-text email bodies (SchedBot-style)."""

from __future__ import annotations

import logging
import re
from typing import Optional

from propgen.config.loader import APIKeys, PropGenConfig
from propgen.models import RawProposalRequest

logger = logging.getLogger(__name__)


class ProposalEmailParser:
    """Heuristic parser; optional AI hook via `ProposalDrafter` elsewhere."""

    def __init__(self, config: PropGenConfig, keys: APIKeys) -> None:
        self.config = config
        self.keys = keys

    async def parse(
        self,
        *,
        subject: str,
        body: str,
        sender_email: str = "",
        sender_name: str = "",
    ) -> RawProposalRequest:
        text = f"{subject}\n{body}"
        lower = text.lower()
        kw = self.config.email_parser.keywords_proposal
        hit = any(k.lower() in lower for k in kw)
        if not hit and not self.config.email_parser.enabled:
            return RawProposalRequest(
                provider="email",
                subject_hint=subject,
                scope_notes=body.strip(),
                client_email=sender_email,
                client_name=sender_name,
                raw_payload={"heuristic": "no_keyword_match"},
            )
        scope = body.strip()
        req = RawProposalRequest(
            provider="email",
            client_email=sender_email,
            client_name=sender_name,
            subject_hint=subject,
            scope_notes=scope,
            raw_payload={"heuristic": "keyword_match" if hit else "forced"},
        )
        return req


def extract_email_address(header_from: str) -> tuple[str, str]:
    """Parse 'Name <email@>' → (name, email)."""
    m = re.search(r"<([^>]+)>", header_from)
    if m:
        email = m.group(1).strip().lower()
        name = header_from.replace(m.group(0), "").strip().strip(" \"'")
        return name, email
    return "", header_from.strip().lower()
