"""Generic inbound webhook — mirrors SchedBot's webhook envelope shape."""

from __future__ import annotations

import hmac
import hashlib
import json
import logging
from typing import Any

from propgen.config.loader import PropGenConfig
from propgen.models import RawProposalRequest

logger = logging.getLogger(__name__)


class ProposalWebhookIngest:
    def __init__(self, config: PropGenConfig, signing_secret: str = "") -> None:
        self.config = config
        self.signing_secret = signing_secret or ""

    def verify(self, body: bytes, headers: dict[str, str]) -> bool:
        if not self.config.webhook.enabled:
            return False
        if not self.signing_secret:
            logger.warning("Webhook signature verification skipped (no secret).")
            return True
        hdr = self.config.webhook.hmac_header
        sig = headers.get(hdr) or headers.get(hdr.lower(), "")
        if not sig:
            return False
        mac = hmac.new(
            self.signing_secret.encode(),
            body,
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(sig, mac) or hmac.compare_digest(
            sig, f"sha256={mac}"
        )

    def parse_json_body(self, body: bytes) -> RawProposalRequest:
        data: dict[str, Any] = json.loads(body.decode("utf-8"))
        return RawProposalRequest(
            provider="webhook",
            provider_event_id=data.get("id"),
            client_name=str(data.get("client_name", "")),
            client_email=str(data.get("client_email", "")),
            client_phone=str(data.get("client_phone", "")),
            subject_hint=str(data.get("subject", "")),
            scope_notes=str(data.get("scope", "") or data.get("message", "")),
            raw_payload=data,
        )
