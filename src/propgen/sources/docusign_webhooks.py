"""DocuSign Connect webhook verification and proposal status updates."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
from dataclasses import dataclass
from typing import Any

import aiosqlite

from propgen._time import now_utc, to_iso
from propgen.crm.database import ProposalDatabase
from propgen.models import AcceptanceEvent, AcceptanceKind, ProposalStatus

logger = logging.getLogger(__name__)


@dataclass
class DocuSignWebhookReceiver:
    """Verify HMAC (Connect HMAC) and map envelope events to proposals."""

    secret: str
    db: ProposalDatabase

    def verify_hmac(self, body: bytes, header_hmac: str | None) -> bool:
        if not self.secret or not header_hmac:
            return not bool(self.secret)
        key = self.secret.encode("utf-8")
        digest = hmac.new(key, body, hashlib.sha256).digest()
        expected = base64.b64encode(digest).decode("ascii")
        return hmac.compare_digest(expected.strip(), header_hmac.strip())

    async def handle(self, body: bytes, headers: dict[str, str]) -> dict[str, Any]:
        sig = headers.get("X-DocuSign-Signature-1") or headers.get("x-docusign-signature-1")
        if not self.verify_hmac(body, sig):
            return {"ok": False, "error": "invalid_hmac"}
        try:
            payload = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as e:
            return {"ok": False, "error": f"invalid_json: {e}"}
        return await self._dispatch(payload)

    async def _dispatch(self, payload: dict[str, Any]) -> dict[str, Any]:
        event = str(payload.get("event", "") or payload.get("Event", "")).lower()
        data = payload.get("data") or payload
        envelope_id = (
            data.get("envelopeId")
            or data.get("EnvelopeId")
            or (data.get("envelopeSummary") or {}).get("envelopeId")
        )
        if not envelope_id:
            return {"ok": True, "ignored": True, "reason": "no_envelope_id"}
        prop = await self._find_by_envelope(str(envelope_id))
        if not prop:
            logger.info("DocuSign event for unknown envelope %s", envelope_id)
            return {"ok": True, "ignored": True, "reason": "proposal_not_found"}

        kind_map = {
            "envelope-sent": AcceptanceKind.SENT,
            "envelope-delivered": AcceptanceKind.VIEWED,
            "envelope-completed": AcceptanceKind.SIGNED,
            "recipient-completed": AcceptanceKind.SIGNED,
            "envelope-declined": AcceptanceKind.DECLINED,
            "envelope-voided": AcceptanceKind.VOIDED,
        }
        akind = kind_map.get(event)
        if akind is None:
            if "sent" in event:
                akind = AcceptanceKind.SENT
            elif "void" in event:
                akind = AcceptanceKind.VOIDED
            else:
                akind = AcceptanceKind.MANUAL_OVERRIDE

        ev = AcceptanceEvent(
            proposal_id=prop.id,
            kind=akind,
            payload_json=json.dumps(payload)[:8000],
        )
        await self.db.append_acceptance_event(ev)

        if akind == AcceptanceKind.VIEWED:
            await self.db.update_proposal_status(prop.id, ProposalStatus.VIEWED)
        elif akind == AcceptanceKind.SIGNED:
            now = to_iso(now_utc())
            await self.db.update_proposal_status(
                prop.id,
                ProposalStatus.ACCEPTED,
                signed_at=now,
                accepted_at=now,
            )
        elif akind == AcceptanceKind.DECLINED:
            await self.db.update_proposal_status(prop.id, ProposalStatus.DECLINED)
        elif akind == AcceptanceKind.VOIDED:
            await self.db.update_proposal_status(prop.id, ProposalStatus.VOIDED)

        return {"ok": True, "proposal_id": prop.id, "event": event}

    async def _find_by_envelope(self, envelope_id: str):
        async with aiosqlite.connect(self.db.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT id FROM proposals WHERE docusign_envelope_id = ? LIMIT 1",
                (envelope_id,),
            ) as cur:
                row = await cur.fetchone()
        if not row:
            return None
        return await self.db.get_proposal(str(row["id"]))
