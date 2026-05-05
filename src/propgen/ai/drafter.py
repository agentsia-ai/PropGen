"""Draft proposal narratives, cover emails, follow-ups, and acceptance notes."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import anthropic

from propgen.config.loader import APIKeys, PropGenConfig
from propgen.models import Proposal, ProposalVersion

logger = logging.getLogger(__name__)

DEFAULT_DRAFTER_PROMPT = """You write proposal copy for a small business. Output is reviewed by a human
before anything is sent.

Rules:
  - Never invent prices not supplied in context. Use the line items given.
  - Use markdown: start with a short cover paragraph, then ## Scope, ## Deliverables, ## Timeline as needed.
  - No signature block in the narrative (PDF footer handles legal).
  - Tone: follow business.communication_tone from context.

Return ONLY JSON:
{"narrative_md":"...", "cover_email_subject":"...", "cover_email_body":"..."}"""


class ProposalDrafter:
    SYSTEM_PROMPT: str = DEFAULT_DRAFTER_PROMPT

    def __init__(self, config: PropGenConfig, keys: APIKeys) -> None:
        self.config = config
        self.client = anthropic.AsyncAnthropic(api_key=keys.anthropic)
        self.model = config.ai.model
        self.max_chars = config.ai.max_narrative_chars
        self._system_prompt = self._load_system_prompt()

    def _load_system_prompt(self) -> str:
        override = self.config.ai.drafter_prompt_path
        if override:
            path = Path(override)
            if path.exists():
                return path.read_text(encoding="utf-8")
        return self.SYSTEM_PROMPT

    async def draft_new_proposal(
        self,
        proposal: Proposal,
        version: ProposalVersion,
        *,
        scope_hint: str = "",
    ) -> tuple[str, str, str]:
        """Returns (narrative_md, cover_subject, cover_body)."""
        items = version.line_items
        item_txt = "\n".join(
            f"- {li.name} ({li.quantity} {li.unit} @ {li.unit_price} {proposal.currency})"
            for li in items
        )
        user = f"""Business: {self.config.business.name}
Operator: {self.config.operator_name}
Communication tone: {self.config.business.communication_tone}
Client: {proposal.client.name} <{proposal.client.email}>
Subject: {proposal.subject}
Scope / notes from intake:
{scope_hint or "(none)"}

Line items (authoritative for pricing copy):
{item_txt or "(none — say pricing is being finalized)"}

Write narrative_md + short cover email proposing they review the PDF.
"""
        raw = await self._json_completion(user)
        nar = str(raw.get("narrative_md", ""))[: self.max_chars]
        subj = str(raw.get("cover_email_subject", self.config.proposal.cover_email_subject_template))
        subj = subj.replace("{{ subject }}", proposal.subject)
        body = str(raw.get("cover_email_body", ""))
        return nar, subj, body

    async def draft_followup(self, proposal: Proposal, *, attempt: int = 1) -> tuple[str, str]:
        user = f"""Draft a polite follow-up email (attempt {attempt}) about an unsigned proposal.
Client: {proposal.client.name}
Subject: {proposal.subject}
Keep under 200 words. Return JSON {{"subject":"...","body":"..."}}"""
        raw = await self._json_completion(user)
        return str(raw.get("subject", "Following up")), str(raw.get("body", ""))

    async def draft_acceptance_welcome(self, proposal: Proposal) -> tuple[str, str]:
        user = f"""The client signed the proposal for {proposal.subject}.
Draft a short welcome / kickoff email. JSON {{"subject":"...","body":"..."}}"""
        raw = await self._json_completion(user)
        return str(raw.get("subject", "Next steps")), str(raw.get("body", ""))

    async def _json_completion(self, user: str) -> dict:
        msg = await self.client.messages.create(
            model=self.model,
            max_tokens=2000,
            system=self._system_prompt,
            messages=[{"role": "user", "content": user}],
        )
        block = msg.content[0]
        text = block.text if hasattr(block, "text") else str(block)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            logger.warning("drafter returned non-JSON")
            return {"narrative_md": text, "cover_email_subject": "Proposal", "cover_email_body": text}
