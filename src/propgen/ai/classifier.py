"""Classify inbound proposal-related messages (email / form text / MCP)."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import anthropic

from propgen._time import now_utc
from propgen.config.loader import APIKeys, PropGenConfig
from propgen.models import ProposalClassification, ProposalRequestKind

logger = logging.getLogger(__name__)

DEFAULT_CLASSIFIER_PROMPT = """You triage inbound messages about proposals, quotes, and estimates.
Classify into exactly one kind:
  - new_proposal: customer wants a quote/proposal for work.
  - revise: they want changes to an existing proposal.
  - question: general question, not an explicit proposal request.
  - pricing_only: they only want a price check / ballpark, not a full doc.
  - uncertain: you cannot tell.

Return ONLY JSON:
{"kind":"<enum value above>","confidence":0.0,"reasoning":"..."}

Be honest about confidence."""


class RequestClassifier:
    SYSTEM_PROMPT: str = DEFAULT_CLASSIFIER_PROMPT

    def __init__(self, config: PropGenConfig, keys: APIKeys) -> None:
        self.config = config
        self.client = anthropic.AsyncAnthropic(api_key=keys.anthropic)
        self.model = config.ai.model
        self.min_confidence = config.ai.min_classification_confidence
        self._system_prompt = self._load_system_prompt()

    def _load_system_prompt(self) -> str:
        override = self.config.ai.classifier_prompt_path
        if override:
            path = Path(override)
            if path.exists():
                logger.info("%s using classifier prompt override: %s", type(self).__name__, path)
                return path.read_text(encoding="utf-8")
            logger.warning("classifier_prompt_path missing: %s", path)
        return self.SYSTEM_PROMPT

    async def classify(
        self,
        message_text: str,
        *,
        sender: str = "",
        subject: str = "",
    ) -> ProposalClassification:
        if not self.config.operator_email:
            pass
        user = f"""Sender: {sender or "(unknown)"}
Subject: {subject or "(none)"}
---
{message_text.strip()}
---
"""
        raw = await self._complete(user)
        try:
            data = json.loads(raw)
            kind = ProposalRequestKind(data.get("kind", "uncertain"))
            conf = float(data.get("confidence", 0))
            reason = str(data.get("reasoning", ""))
        except (json.JSONDecodeError, ValueError):
            kind = ProposalRequestKind.UNCERTAIN
            conf = 0.0
            reason = "parse_error"
        if conf < self.min_confidence:
            kind = ProposalRequestKind.UNCERTAIN
        return ProposalClassification(kind=kind, confidence=conf, reasoning=reason, classified_at=now_utc())

    async def _complete(self, user: str) -> str:
        msg = await self.client.messages.create(
            model=self.model,
            max_tokens=500,
            system=self._system_prompt,
            messages=[{"role": "user", "content": user}],
        )
        block = msg.content[0]
        return block.text if hasattr(block, "text") else str(block)
