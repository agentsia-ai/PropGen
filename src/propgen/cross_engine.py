"""Read-only access to sibling engine SQLite databases (LeadGen, SchedBot).

PropGen never imports `leadgen` or `schedbot` packages — only documented
`SELECT`s against known tables. Any failure returns `None`.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import aiosqlite

from propgen.config.loader import PropGenConfig

logger = logging.getLogger(__name__)


@dataclass
class LeadSnapshot:
    lead_id: str
    contact_name: str
    contact_email: str
    contact_phone: str
    company_name: str
    notes: str
    tags: list[str]
    status: str
    source: str


@dataclass
class AppointmentSnapshot:
    appointment_id: str
    client_name: str
    client_email: str
    client_phone: str
    service_slug: str
    service_name: str
    intake_notes: str
    notes: str
    start_at_iso: Optional[str]
    end_at_iso: Optional[str]


async def fetch_lead(config: PropGenConfig, lead_id: str) -> Optional[LeadSnapshot]:
    path = config.cross_engine.leadgen_db
    if not path:
        return None
    p = Path(path)
    if not p.is_file():
        logger.debug("LeadGen DB not found at %s", p)
        return None
    try:
        async with aiosqlite.connect(p) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT id, source, status, contact_json, company_json,
                       notes, tags_json
                FROM leads
                WHERE id = ?
                """,
                (lead_id,),
            ) as cur:
                row = await cur.fetchone()
        if not row:
            return None
        contact = _safe_json_dict(row["contact_json"])
        company = _safe_json_dict(row["company_json"])
        tags_raw = row["tags_json"] or "[]"
        try:
            tags = json.loads(tags_raw)
            if not isinstance(tags, list):
                tags = []
        except json.JSONDecodeError:
            tags = []
        return LeadSnapshot(
            lead_id=str(row["id"]),
            contact_name=str(contact.get("name") or ""),
            contact_email=str(contact.get("email") or "").strip().lower(),
            contact_phone=str(contact.get("phone") or ""),
            company_name=str(company.get("name") or ""),
            notes=str(row["notes"] or ""),
            tags=[str(t) for t in tags],
            status=str(row["status"] or ""),
            source=str(row["source"] or ""),
        )
    except Exception as e:  # noqa: BLE001 — deliberate broad catch
        logger.warning("fetch_lead failed for %s: %s", lead_id, e)
        return None


async def fetch_appointment(
    config: PropGenConfig, appt_id: str
) -> Optional[AppointmentSnapshot]:
    path = config.cross_engine.schedbot_db
    if not path:
        return None
    p = Path(path)
    if not p.is_file():
        logger.debug("SchedBot DB not found at %s", p)
        return None
    try:
        async with aiosqlite.connect(p) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT id, client_name, client_email, client_phone,
                       service_slug, service_name, intake_notes, notes,
                       start_at, end_at
                FROM appointments
                WHERE id = ?
                """,
                (appt_id,),
            ) as cur:
                row = await cur.fetchone()
        if not row:
            return None
        return AppointmentSnapshot(
            appointment_id=str(row["id"]),
            client_name=str(row["client_name"] or ""),
            client_email=str(row["client_email"] or "").strip().lower(),
            client_phone=str(row["client_phone"] or ""),
            service_slug=str(row["service_slug"] or ""),
            service_name=str(row["service_name"] or ""),
            intake_notes=str(row["intake_notes"] or ""),
            notes=str(row["notes"] or ""),
            start_at_iso=row["start_at"],
            end_at_iso=row["end_at"],
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("fetch_appointment failed for %s: %s", appt_id, e)
        return None


def _safe_json_dict(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}
