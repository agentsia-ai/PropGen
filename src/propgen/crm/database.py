"""Async SQLite store for proposals, versions, follow-ups, and audit events."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

import aiosqlite

from propgen._time import now_utc, parse_iso, to_iso
from propgen.models import (
    AcceptanceEvent,
    AcceptanceKind,
    ClientInfo,
    FollowUpChannel,
    FollowUpRecord,
    FollowUpStatus,
    LineItem,
    Proposal,
    ProposalSource,
    ProposalStatus,
    ProposalVersion,
    VersionDraftStatus,
)

logger = logging.getLogger(__name__)


class ProposalDatabase:
    def __init__(self, db_path: str = "./data/propgen.db") -> None:
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    async def init(self) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS proposals (
                    id TEXT PRIMARY KEY,
                    source TEXT NOT NULL DEFAULT 'manual',
                    status TEXT NOT NULL DEFAULT 'drafted',
                    client_name TEXT DEFAULT '',
                    client_email TEXT DEFAULT '',
                    client_phone TEXT DEFAULT '',
                    lead_id TEXT,
                    appointment_id TEXT,
                    subject TEXT DEFAULT '',
                    currency TEXT DEFAULT 'USD',
                    subtotal REAL DEFAULT 0,
                    tax_amount REAL DEFAULT 0,
                    discount_amount REAL DEFAULT 0,
                    total REAL DEFAULT 0,
                    expires_at TEXT,
                    docusign_envelope_id TEXT,
                    current_version_id TEXT,
                    cover_email_subject TEXT DEFAULT '',
                    cover_email_body TEXT DEFAULT '',
                    send_approval_token TEXT NOT NULL,
                    send_approved_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    accepted_at TEXT,
                    signed_at TEXT,
                    raw_data_json TEXT DEFAULT '{}'
                )
                """
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_proposals_status ON proposals(status)"
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS proposal_versions (
                    id TEXT PRIMARY KEY,
                    proposal_id TEXT NOT NULL,
                    version_number INTEGER NOT NULL,
                    narrative_md TEXT DEFAULT '',
                    line_items_json TEXT DEFAULT '[]',
                    pdf_path TEXT DEFAULT '',
                    draft_status TEXT NOT NULL DEFAULT 'draft',
                    drafted_by TEXT DEFAULT 'ai',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (proposal_id) REFERENCES proposals(id)
                )
                """
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_versions_proposal "
                "ON proposal_versions(proposal_id)"
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS followups (
                    id TEXT PRIMARY KEY,
                    proposal_id TEXT NOT NULL,
                    channel TEXT NOT NULL DEFAULT 'email',
                    scheduled_for TEXT,
                    sent_at TEXT,
                    draft_subject TEXT DEFAULT '',
                    draft_body TEXT DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'drafted',
                    approval_token TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (proposal_id) REFERENCES proposals(id)
                )
                """
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_followups_proposal "
                "ON followups(proposal_id)"
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS acceptance_events (
                    id TEXT PRIMARY KEY,
                    proposal_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    payload_json TEXT DEFAULT '{}',
                    FOREIGN KEY (proposal_id) REFERENCES proposals(id)
                )
                """
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_events_proposal "
                "ON acceptance_events(proposal_id)"
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS raw_proposal_requests (
                    id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    provider_event_id TEXT,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            await db.commit()
        logger.info("Database initialized: %s", self.db_path)

    async def upsert_proposal(self, p: Proposal) -> Proposal:
        p.touch()
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT id FROM proposals WHERE id = ?", (p.id,)) as cur:
                ex = await cur.fetchone()
            if ex:
                await db.execute(
                    """
                    UPDATE proposals SET
                      source=?, status=?, client_name=?, client_email=?,
                      client_phone=?, lead_id=?, appointment_id=?, subject=?,
                      currency=?, subtotal=?, tax_amount=?, discount_amount=?,
                      total=?, expires_at=?, docusign_envelope_id=?,
                      current_version_id=?, cover_email_subject=?,
                      cover_email_body=?, send_approval_token=?,
                      send_approved_at=?, updated_at=?, accepted_at=?,
                      signed_at=?, raw_data_json=?
                    WHERE id=?
                    """,
                    _proposal_update_tuple(p) + (p.id,),
                )
            else:
                await db.execute(
                    """
                    INSERT INTO proposals
                      (id, source, status, client_name, client_email,
                       client_phone, lead_id, appointment_id, subject,
                       currency, subtotal, tax_amount, discount_amount,
                       total, expires_at, docusign_envelope_id,
                       current_version_id, cover_email_subject,
                       cover_email_body, send_approval_token,
                       send_approved_at, created_at, updated_at,
                       accepted_at, signed_at, raw_data_json)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    _proposal_insert_tuple(p),
                )
            await db.commit()
        return p

    async def get_proposal(self, proposal_id: str) -> Optional[Proposal]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM proposals WHERE id = ?", (proposal_id,)
            ) as cur:
                row = await cur.fetchone()
        return _row_to_proposal(row) if row else None

    async def find_proposal_id_prefix(self, prefix: str) -> Optional[str]:
        pref = prefix.strip()
        if not pref:
            return None
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT id FROM proposals WHERE id LIKE ? LIMIT 2",
                (f"{pref}%",),
            ) as cur:
                rows = await cur.fetchall()
        if len(rows) == 1:
            return str(rows[0][0])
        return None

    async def list_proposals(
        self,
        status: Optional[ProposalStatus] = None,
        limit: int = 50,
    ) -> list[Proposal]:
        q = "SELECT * FROM proposals"
        params: list[Any] = []
        if status:
            q += " WHERE status = ?"
            params.append(status.value)
        q += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(q, params) as cur:
                rows = await cur.fetchall()
        return [_row_to_proposal(r) for r in rows]

    async def insert_version(self, v: ProposalVersion) -> ProposalVersion:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO proposal_versions
                  (id, proposal_id, version_number, narrative_md,
                   line_items_json, pdf_path, draft_status, drafted_by, created_at)
                VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    v.id,
                    v.proposal_id,
                    v.version_number,
                    v.narrative_md,
                    json.dumps([li.model_dump(mode="json") for li in v.line_items]),
                    v.pdf_path,
                    v.draft_status.value,
                    v.drafted_by,
                    to_iso(v.created_at),
                ),
            )
            await db.commit()
        return v

    async def get_version(self, version_id: str) -> Optional[ProposalVersion]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM proposal_versions WHERE id = ?", (version_id,)
            ) as cur:
                row = await cur.fetchone()
        return _row_to_version(row) if row else None

    async def get_current_version(self, proposal_id: str) -> Optional[ProposalVersion]:
        p = await self.get_proposal(proposal_id)
        if not p or not p.current_version_id:
            return None
        return await self.get_version(p.current_version_id)

    async def update_version(self, v: ProposalVersion) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE proposal_versions SET
                  narrative_md=?, line_items_json=?, pdf_path=?,
                  draft_status=?, drafted_by=?
                WHERE id=?
                """,
                (
                    v.narrative_md,
                    json.dumps([li.model_dump(mode="json") for li in v.line_items]),
                    v.pdf_path,
                    v.draft_status.value,
                    v.drafted_by,
                    v.id,
                ),
            )
            await db.commit()

    async def try_send_lock(
        self,
        proposal_id: str,
        approval_token: str,
        envelope_id: str,
    ) -> bool:
        """Atomically transition DRAFTED → SENT if token matches and approved."""
        new_tok = str(uuid4())
        now = to_iso(now_utc())
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute(
                """
                UPDATE proposals SET
                  status = 'sent',
                  docusign_envelope_id = ?,
                  updated_at = ?,
                  send_approval_token = ?
                WHERE id = ?
                  AND send_approval_token = ?
                  AND status = 'drafted'
                  AND send_approved_at IS NOT NULL
                  AND current_version_id IS NOT NULL
                  AND EXISTS (
                    SELECT 1 FROM proposal_versions v
                    WHERE v.id = proposals.current_version_id
                      AND v.draft_status = 'approved'
                  )
                """,
                (envelope_id, now, new_tok, proposal_id, approval_token),
            )
            await db.commit()
            return cur.rowcount == 1

    async def update_proposal_status(
        self,
        proposal_id: str,
        status: ProposalStatus,
        *,
        signed_at: Optional[str] = None,
        accepted_at: Optional[str] = None,
    ) -> None:
        sets = "status = ?, updated_at = ?"
        params: list[Any] = [status.value, to_iso(now_utc())]
        if signed_at is not None:
            sets += ", signed_at = ?"
            params.append(signed_at)
        if accepted_at is not None:
            sets += ", accepted_at = ?"
            params.append(accepted_at)
        params.append(proposal_id)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(f"UPDATE proposals SET {sets} WHERE id = ?", params)
            await db.commit()

    async def append_acceptance_event(self, ev: AcceptanceEvent) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO acceptance_events
                  (id, proposal_id, kind, occurred_at, payload_json)
                VALUES (?,?,?,?,?)
                """,
                (ev.id, ev.proposal_id, ev.kind.value, to_iso(ev.occurred_at), ev.payload_json),
            )
            await db.commit()

    async def list_acceptance_events(self, proposal_id: str) -> list[AcceptanceEvent]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT * FROM acceptance_events
                WHERE proposal_id = ? ORDER BY occurred_at ASC
                """,
                (proposal_id,),
            ) as cur:
                rows = await cur.fetchall()
        return [_row_to_event(r) for r in rows]

    async def insert_followup(self, f: FollowUpRecord) -> FollowUpRecord:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO followups
                  (id, proposal_id, channel, scheduled_for, sent_at,
                   draft_subject, draft_body, status, approval_token, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    f.id,
                    f.proposal_id,
                    f.channel.value,
                    to_iso(f.scheduled_for),
                    to_iso(f.sent_at),
                    f.draft_subject,
                    f.draft_body,
                    f.status.value,
                    f.approval_token,
                    to_iso(f.created_at),
                ),
            )
            await db.commit()
        return f

    async def list_followups(
        self,
        proposal_id: str,
        status: Optional[FollowUpStatus] = None,
    ) -> list[FollowUpRecord]:
        q = "SELECT * FROM followups WHERE proposal_id = ?"
        params: list[Any] = [proposal_id]
        if status:
            q += " AND status = ?"
            params.append(status.value)
        q += " ORDER BY scheduled_for IS NULL, scheduled_for, created_at"
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(q, params) as cur:
                rows = await cur.fetchall()
        return [_row_to_followup(r) for r in rows]

    async def update_followup(self, f: FollowUpRecord) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE followups SET
                  scheduled_for=?, sent_at=?, draft_subject=?, draft_body=?,
                  status=?
                WHERE id=?
                """,
                (
                    to_iso(f.scheduled_for),
                    to_iso(f.sent_at),
                    f.draft_subject,
                    f.draft_body,
                    f.status.value,
                    f.id,
                ),
            )
            await db.commit()

    async def pipeline_counts(self) -> dict[str, int]:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT status, COUNT(*) FROM proposals GROUP BY status"
            ) as cur:
                rows = await cur.fetchall()
        return {str(r[0]): int(r[1]) for r in rows}

    async def iter_stale_sent(self, cutoff_iso: str) -> list[Proposal]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT * FROM proposals
                WHERE status IN ('sent', 'viewed')
                  AND expires_at IS NOT NULL
                  AND expires_at < ?
                ORDER BY updated_at DESC
                """,
                (cutoff_iso,),
            ) as cur:
                rows = await cur.fetchall()
        return [_row_to_proposal(r) for r in rows]


def _proposal_insert_tuple(p: Proposal) -> tuple:
    return (
        p.id,
        p.source.value,
        p.status.value,
        p.client.name,
        p.client.email,
        p.client.phone,
        p.lead_id,
        p.appointment_id,
        p.subject,
        p.currency,
        p.subtotal,
        p.tax_amount,
        p.discount_amount,
        p.total,
        to_iso(p.expires_at),
        p.docusign_envelope_id,
        p.current_version_id,
        p.cover_email_subject,
        p.cover_email_body,
        p.send_approval_token,
        to_iso(p.send_approved_at),
        to_iso(p.created_at),
        to_iso(p.updated_at),
        to_iso(p.accepted_at),
        to_iso(p.signed_at),
        json.dumps(p.raw_data),
    )


def _proposal_update_tuple(p: Proposal) -> tuple:
    return (
        p.source.value,
        p.status.value,
        p.client.name,
        p.client.email,
        p.client.phone,
        p.lead_id,
        p.appointment_id,
        p.subject,
        p.currency,
        p.subtotal,
        p.tax_amount,
        p.discount_amount,
        p.total,
        to_iso(p.expires_at),
        p.docusign_envelope_id,
        p.current_version_id,
        p.cover_email_subject,
        p.cover_email_body,
        p.send_approval_token,
        to_iso(p.send_approved_at),
        to_iso(p.updated_at),
        to_iso(p.accepted_at),
        to_iso(p.signed_at),
        json.dumps(p.raw_data),
    )


def _row_to_proposal(row: aiosqlite.Row) -> Proposal:
    return Proposal(
        id=row["id"],
        source=ProposalSource(row["source"]),
        status=ProposalStatus(row["status"]),
        client=ClientInfo(
            name=row["client_name"] or "",
            email=row["client_email"] or "",
            phone=row["client_phone"] or "",
        ),
        lead_id=row["lead_id"],
        appointment_id=row["appointment_id"],
        subject=row["subject"] or "",
        currency=row["currency"] or "USD",
        subtotal=float(row["subtotal"] or 0),
        tax_amount=float(row["tax_amount"] or 0),
        discount_amount=float(row["discount_amount"] or 0),
        total=float(row["total"] or 0),
        expires_at=parse_iso(row["expires_at"]),
        docusign_envelope_id=row["docusign_envelope_id"],
        current_version_id=row["current_version_id"],
        cover_email_subject=row["cover_email_subject"] or "",
        cover_email_body=row["cover_email_body"] or "",
        send_approval_token=row["send_approval_token"] or "",
        send_approved_at=parse_iso(row["send_approved_at"]),
        created_at=parse_iso(row["created_at"]) or now_utc(),
        updated_at=parse_iso(row["updated_at"]) or now_utc(),
        accepted_at=parse_iso(row["accepted_at"]),
        signed_at=parse_iso(row["signed_at"]),
        raw_data=json.loads(row["raw_data_json"] or "{}"),
    )


def _row_to_version(row: aiosqlite.Row) -> ProposalVersion:
    try:
        items_raw = json.loads(row["line_items_json"] or "[]")
        items = [LineItem.model_validate(x) for x in items_raw]
    except Exception:  # noqa: BLE001
        items = []
    return ProposalVersion(
        id=row["id"],
        proposal_id=row["proposal_id"],
        version_number=int(row["version_number"]),
        narrative_md=row["narrative_md"] or "",
        line_items=items,
        pdf_path=row["pdf_path"] or "",
        draft_status=VersionDraftStatus(row["draft_status"]),
        drafted_by=row["drafted_by"] or "ai",
        created_at=parse_iso(row["created_at"]) or now_utc(),
    )


def _row_to_followup(row: aiosqlite.Row) -> FollowUpRecord:
    return FollowUpRecord(
        id=row["id"],
        proposal_id=row["proposal_id"],
        channel=FollowUpChannel(row["channel"]),
        scheduled_for=parse_iso(row["scheduled_for"]),
        sent_at=parse_iso(row["sent_at"]),
        draft_subject=row["draft_subject"] or "",
        draft_body=row["draft_body"] or "",
        status=FollowUpStatus(row["status"]),
        approval_token=row["approval_token"] or "",
        created_at=parse_iso(row["created_at"]) or now_utc(),
    )


def _row_to_event(row: aiosqlite.Row) -> AcceptanceEvent:
    return AcceptanceEvent(
        id=row["id"],
        proposal_id=row["proposal_id"],
        kind=AcceptanceKind(row["kind"]),
        occurred_at=parse_iso(row["occurred_at"]) or now_utc(),
        payload_json=row["payload_json"] or "{}",
    )
