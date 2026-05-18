"""High-level proposal operations shared by CLI and MCP."""

from __future__ import annotations

import logging
from datetime import timedelta
from email.message import EmailMessage
from pathlib import Path
from typing import Optional, Type
from uuid import uuid4

import asyncio
import aiosqlite
import aiosmtplib

from propgen._time import now_utc, to_iso
from propgen.ai.drafter import ProposalDrafter
from propgen.config.loader import APIKeys, PropGenConfig
from propgen.cross_engine import fetch_appointment, fetch_lead
from propgen.crm.database import ProposalDatabase
from propgen.followup.scheduler import enqueue_cadence_followups
from propgen.models import (
    AcceptanceEvent,
    AcceptanceKind,
    ClientInfo,
    FollowUpRecord,
    FollowUpStatus,
    LineItem,
    Proposal,
    ProposalSource,
    ProposalStatus,
    ProposalVersion,
    VersionDraftStatus,
)
from propgen.pdf.renderer import render_proposal_pdf
from propgen.pricing.catalog import catalog_by_slug, compute_totals, line_items_from_slugs
from propgen.sources import docusign as docusign_api

logger = logging.getLogger(__name__)


async def resolve_proposal_id(db: ProposalDatabase, prefix: str) -> Optional[str]:
    p = prefix.strip()
    if not p:
        return None
    direct = await db.get_proposal(p)
    if direct:
        return direct.id
    return await db.find_proposal_id_prefix(p)


async def send_smtp_message(
    config: PropGenConfig,
    keys: APIKeys,
    *,
    to_email: str,
    subject: str,
    body: str,
) -> bool:
    if not keys.smtp_username or not keys.smtp_password:
        logger.warning("SMTP not configured — skipping email send.")
        return False
    host = keys.smtp_host or config.outreach.smtp_host
    port = int(keys.smtp_port or config.outreach.smtp_port)
    from_addr = (
        keys.smtp_from_email
        or config.outreach.from_address
        or config.operator_email
    )
    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body)
    await aiosmtplib.send(
        msg,
        hostname=host,
        port=port,
        username=keys.smtp_username,
        password=keys.smtp_password,
        start_tls=config.outreach.smtp_starttls,
    )
    return True


def _default_line_items(config: PropGenConfig) -> list[LineItem]:
    cat = catalog_by_slug(config)
    if not cat:
        return []
    first = next(iter(cat.values()))
    return [
        LineItem(
            name=first.name,
            description=first.description,
            quantity=1,
            unit=first.unit,
            unit_price=first.unit_price,
            line_total=first.unit_price,
            taxable=first.taxable,
            sort_order=0,
        )
    ]


def _apply_totals_to_proposal(
    prop: Proposal, items: list[LineItem], config: PropGenConfig
) -> None:
    sub, tax, _, tot = compute_totals(
        items,
        prop.currency,
        float(config.pricing.default_tax_rate),
        discount=prop.discount_amount,
    )
    prop.subtotal = sub
    prop.tax_amount = tax
    prop.total = tot


async def create_proposal_version1(
    config: PropGenConfig,
    db: ProposalDatabase,
    *,
    source: ProposalSource,
    client: ClientInfo,
    subject: str,
    line_items: list[LineItem],
    lead_id: Optional[str] = None,
    appointment_id: Optional[str] = None,
    scope_hint: str = "",
    drafter_cls: Type[ProposalDrafter] = ProposalDrafter,
    keys: APIKeys | None = None,
) -> tuple[Proposal, ProposalVersion]:
    keys = keys or APIKeys.from_env()
    prop = Proposal(
        source=source,
        client=client,
        subject=subject,
        currency=config.pricing.currency,
        lead_id=lead_id,
        appointment_id=appointment_id,
    )
    prop.expires_at = now_utc() + timedelta(days=config.proposal.valid_for_days)
    _apply_totals_to_proposal(prop, line_items, config)
    ver = ProposalVersion(
        proposal_id=prop.id,
        version_number=1,
        line_items=line_items,
        narrative_md="# Proposal\n\n",
        draft_status=VersionDraftStatus.DRAFT,
    )
    prop.current_version_id = ver.id
    await db.insert_version(ver)
    await db.upsert_proposal(prop)
    drafter = drafter_cls(config, keys)
    nar, csub, cbody = await drafter.draft_new_proposal(prop, ver, scope_hint=scope_hint)
    ver.narrative_md = nar
    await db.update_version(ver)
    tmpl = config.proposal.cover_email_subject_template
    prop.cover_email_subject = csub or tmpl.replace("{{ subject }}", prop.subject)
    prop.cover_email_body = cbody
    await db.upsert_proposal(prop)
    pdf_dir = Path(config.database.pdf_dir)
    pdf_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = pdf_dir / f"{prop.id}_{ver.id}.pdf"
    await render_proposal_pdf(prop, ver, config, pdf_path)
    try:
        ver.pdf_path = str(pdf_path.resolve().relative_to(Path.cwd()))
    except ValueError:
        ver.pdf_path = str(pdf_path)
    await db.update_version(ver)
    return prop, ver


async def create_proposal_from_lead(
    config: PropGenConfig,
    keys: APIKeys,
    db: ProposalDatabase,
    lead_id: str,
    drafter_cls: Type[ProposalDrafter] = ProposalDrafter,
) -> Optional[tuple[Proposal, ProposalVersion]]:
    snap = await fetch_lead(config, lead_id)
    if snap is None:
        return None
    client = ClientInfo(
        name=snap.contact_name,
        email=snap.contact_email,
        phone=snap.contact_phone,
    )
    scope = "\n".join(x for x in [snap.notes, f"Company: {snap.company_name}"] if x)
    items = _default_line_items(config)
    return await create_proposal_version1(
        config,
        db,
        source=ProposalSource.LEADGEN,
        client=client,
        subject=f"Proposal for {snap.company_name or client.name or client.email}",
        line_items=items,
        lead_id=lead_id,
        scope_hint=scope,
        drafter_cls=drafter_cls,
        keys=keys,
    )


async def create_proposal_from_appointment(
    config: PropGenConfig,
    keys: APIKeys,
    db: ProposalDatabase,
    appointment_id: str,
    drafter_cls: Type[ProposalDrafter] = ProposalDrafter,
) -> Optional[tuple[Proposal, ProposalVersion]]:
    snap = await fetch_appointment(config, appointment_id)
    if snap is None:
        return None
    client = ClientInfo(
        name=snap.client_name,
        email=snap.client_email,
        phone=snap.client_phone,
    )
    scope = "\n".join(
        x
        for x in [
            snap.intake_notes,
            snap.notes,
            f"Service: {snap.service_name} ({snap.service_slug})",
        ]
        if x
    )
    cat = catalog_by_slug(config)
    items: list[LineItem] = []
    if snap.service_slug and snap.service_slug in cat:
        e = cat[snap.service_slug]
        items.append(
            LineItem(
                name=e.name,
                description=e.description,
                quantity=1,
                unit=e.unit,
                unit_price=e.unit_price,
                line_total=e.unit_price,
                taxable=e.taxable,
                sort_order=0,
            )
        )
    if not items:
        items = _default_line_items(config)
    return await create_proposal_version1(
        config,
        db,
        source=ProposalSource.SCHEDBOT,
        client=client,
        subject=f"Proposal — {snap.service_name or 'services'}",
        line_items=items,
        appointment_id=appointment_id,
        scope_hint=scope,
        drafter_cls=drafter_cls,
        keys=keys,
    )


async def create_proposal_explicit(
    config: PropGenConfig,
    keys: APIKeys,
    db: ProposalDatabase,
    *,
    client_name: str,
    client_email: str,
    client_phone: str = "",
    subject: str,
    catalog_slugs: list[tuple[str, float]],
    drafter_cls: Type[ProposalDrafter] = ProposalDrafter,
) -> tuple[Proposal, ProposalVersion]:
    items = line_items_from_slugs(config, catalog_slugs) or _default_line_items(config)
    client = ClientInfo(name=client_name, email=client_email, phone=client_phone)
    return await create_proposal_version1(
        config,
        db,
        source=ProposalSource.MANUAL,
        client=client,
        subject=subject,
        line_items=items,
        drafter_cls=drafter_cls,
        keys=keys,
    )


async def _count_versions(db: ProposalDatabase, proposal_id: str) -> int:
    async with aiosqlite.connect(db.db_path) as conn:
        async with conn.execute(
            "SELECT COUNT(*) FROM proposal_versions WHERE proposal_id = ?",
            (proposal_id,),
        ) as cur:
            row = await cur.fetchone()
    return int(row[0]) if row else 0


async def revise_proposal(
    config: PropGenConfig,
    keys: APIKeys,
    db: ProposalDatabase,
    proposal_id: str,
    guidance: str = "",
    drafter_cls: Type[ProposalDrafter] = ProposalDrafter,
) -> Optional[ProposalVersion]:
    prop = await db.get_proposal(proposal_id)
    if not prop or not prop.current_version_id:
        return None
    cur = await db.get_version(prop.current_version_id)
    if not cur:
        return None
    n = await _count_versions(db, proposal_id)
    ver = ProposalVersion(
        proposal_id=prop.id,
        version_number=n + 1,
        line_items=list(cur.line_items),
        narrative_md=cur.narrative_md,
        draft_status=VersionDraftStatus.DRAFT,
    )
    await db.insert_version(ver)
    prop.current_version_id = ver.id
    prop.status = ProposalStatus.DRAFTED
    prop.send_approved_at = None
    await db.upsert_proposal(prop)
    drafter = drafter_cls(config, keys)
    nar, csub, cbody = await drafter.draft_new_proposal(
        prop, ver, scope_hint=guidance or "Revise per operator guidance."
    )
    ver.narrative_md = nar
    await db.update_version(ver)
    prop.cover_email_subject = csub
    prop.cover_email_body = cbody
    await db.upsert_proposal(prop)
    pdf_dir = Path(config.database.pdf_dir)
    pdf_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = pdf_dir / f"{prop.id}_{ver.id}.pdf"
    await render_proposal_pdf(prop, ver, config, pdf_path)
    try:
        ver.pdf_path = str(pdf_path.resolve().relative_to(Path.cwd()))
    except ValueError:
        ver.pdf_path = str(pdf_path)
    await db.update_version(ver)
    return ver


async def render_current_pdf(
    config: PropGenConfig,
    db: ProposalDatabase,
    proposal_id: str,
) -> Optional[Path]:
    prop = await db.get_proposal(proposal_id)
    if not prop or not prop.current_version_id:
        return None
    ver = await db.get_version(prop.current_version_id)
    if not ver:
        return None
    pdf_dir = Path(config.database.pdf_dir)
    pdf_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = pdf_dir / f"{prop.id}_{ver.id}.pdf"
    await render_proposal_pdf(prop, ver, config, pdf_path)
    try:
        ver.pdf_path = str(pdf_path.resolve().relative_to(Path.cwd()))
    except ValueError:
        ver.pdf_path = str(pdf_path)
    await db.update_version(ver)
    return pdf_path


async def approve_for_send(
    db: ProposalDatabase,
    proposal_id: str,
) -> Optional[tuple[Proposal, str]]:
    prop = await db.get_proposal(proposal_id)
    if not prop or not prop.current_version_id:
        return None
    ver = await db.get_version(prop.current_version_id)
    if not ver:
        return None
    ver.draft_status = VersionDraftStatus.APPROVED
    await db.update_version(ver)
    prop.send_approval_token = str(uuid4())
    prop.send_approved_at = now_utc()
    prop.status = ProposalStatus.DRAFTED
    await db.upsert_proposal(prop)
    return prop, prop.send_approval_token


async def send_proposal(
    config: PropGenConfig,
    keys: APIKeys,
    db: ProposalDatabase,
    proposal_id: str,
    approval_token: str,
) -> dict:
    prop = await db.get_proposal(proposal_id)
    if not prop:
        return {"ok": False, "error": "not_found"}
    # Idempotent retries: token is rotated after send; check before approval gate.
    if prop.status == ProposalStatus.SENT and prop.docusign_envelope_id:
        return {
            "ok": True,
            "already_sent": True,
            "envelope_id": prop.docusign_envelope_id,
            "proposal_id": proposal_id,
        }
    if config.proposal.require_approval:
        if approval_token != prop.send_approval_token:
            return {"ok": False, "error": "approval_token_mismatch"}
        if prop.send_approved_at is None:
            return {"ok": False, "error": "not_approved"}
    ver = await db.get_current_version(proposal_id)
    if not ver or ver.draft_status != VersionDraftStatus.APPROVED:
        return {"ok": False, "error": "version_not_approved"}
    pdf_path = Path(ver.pdf_path)
    if not pdf_path.is_file():
        pdf_path = Path(config.database.pdf_dir) / f"{prop.id}_{ver.id}.pdf"
        await render_proposal_pdf(prop, ver, config, pdf_path)
        ver.pdf_path = str(pdf_path)
        await db.update_version(ver)
    subj = config.docusign.envelope_email_subject.replace("{{ subject }}", prop.subject)
    body = config.docusign.envelope_email_body
    pdf_bytes = await asyncio.to_thread(pdf_path.read_bytes)
    try:
        env_id = await docusign_api.send_envelope_for_pdf(
            config,
            keys,
            pdf_bytes=pdf_bytes,
            document_name=f"proposal-{prop.id[:8]}.pdf",
            signer_name=prop.client.name or "Signer",
            signer_email=prop.client.email,
            email_subject=subj,
            email_body=body,
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("DocuSign send failed")
        return {"ok": False, "error": str(e)}
    lock_ok = await db.try_send_lock(proposal_id, approval_token, env_id)
    we_won_send_lock = lock_ok
    if not lock_ok:
        fresh = await db.get_proposal(proposal_id)
        if (
            fresh
            and fresh.status == ProposalStatus.SENT
            and fresh.docusign_envelope_id
        ):
            env_id = fresh.docusign_envelope_id
            prop = fresh
            we_won_send_lock = False
        else:
            return {"ok": False, "error": "send_lock_failed_or_race"}
    else:
        fresh = await db.get_proposal(proposal_id)

    if we_won_send_lock:
        await db.append_acceptance_event(
            AcceptanceEvent(proposal_id=proposal_id, kind=AcceptanceKind.SENT)
        )
    if we_won_send_lock and prop.cover_email_subject and prop.client.email:
        await send_smtp_message(
            config,
            keys,
            to_email=prop.client.email,
            subject=prop.cover_email_subject,
            body=prop.cover_email_body,
        )
    final = fresh or await db.get_proposal(proposal_id)
    if final:
        await enqueue_cadence_followups(config, db, final)
    return {"ok": True, "envelope_id": env_id, "proposal_id": proposal_id}


async def record_signed_manual(db: ProposalDatabase, proposal_id: str) -> bool:
    prop = await db.get_proposal(proposal_id)
    if not prop:
        return False
    now = to_iso(now_utc())
    await db.update_proposal_status(
        prop.id,
        ProposalStatus.ACCEPTED,
        signed_at=now,
        accepted_at=now,
    )
    await db.append_acceptance_event(
        AcceptanceEvent(
            proposal_id=prop.id,
            kind=AcceptanceKind.MANUAL_OVERRIDE,
            payload_json='{"reason":"wet_signature"}',
        )
    )
    return True


async def mark_declined(db: ProposalDatabase, proposal_id: str) -> bool:
    prop = await db.get_proposal(proposal_id)
    if not prop:
        return False
    await db.update_proposal_status(prop.id, ProposalStatus.DECLINED)
    await db.append_acceptance_event(
        AcceptanceEvent(proposal_id=prop.id, kind=AcceptanceKind.DECLINED)
    )
    return True


async def sweep_expired(config: PropGenConfig, db: ProposalDatabase) -> int:
    cutoff = to_iso(now_utc())
    stale = await db.iter_stale_sent(cutoff)
    n = 0
    for p in stale:
        await db.update_proposal_status(p.id, ProposalStatus.EXPIRED)
        n += 1
    return n


async def draft_next_followup(
    config: PropGenConfig,
    keys: APIKeys,
    db: ProposalDatabase,
    proposal_id: str,
    drafter_cls: Type[ProposalDrafter] = ProposalDrafter,
) -> Optional[FollowUpRecord]:
    recs = await db.list_followups(proposal_id, status=FollowUpStatus.DRAFTED)
    if not recs:
        return None
    rec = recs[0]
    prop = await db.get_proposal(proposal_id)
    if not prop:
        return None
    drafter = drafter_cls(config, keys)
    subj, body = await drafter.draft_followup(prop, attempt=1)
    rec.draft_subject = subj
    rec.draft_body = body
    await db.update_followup(rec)
    return rec
