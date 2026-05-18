"""Enqueue and schedule proposal follow-ups (human-approval-gated sends)."""

from __future__ import annotations

from datetime import timedelta

from propgen._time import now_utc
from propgen.config.loader import PropGenConfig
from propgen.crm.database import ProposalDatabase
from propgen.models import FollowUpRecord, FollowUpStatus, Proposal, ProposalStatus


async def enqueue_cadence_followups(
    config: PropGenConfig,
    db: ProposalDatabase,
    proposal: Proposal,
) -> list[FollowUpRecord]:
    """Create follow-up rows from config cadence (DRAFTED, no body until draft_followup)."""
    if proposal.status not in {ProposalStatus.SENT, ProposalStatus.VIEWED}:
        return []
    existing = await db.list_followups(proposal.id)
    if existing:
        return []
    out: list[FollowUpRecord] = []
    base = proposal.updated_at or now_utc()
    for days in config.proposal.follow_up_cadence_days:
        due = base + timedelta(days=days)
        rec = FollowUpRecord(
            proposal_id=proposal.id,
            scheduled_for=due,
            status=FollowUpStatus.DRAFTED,
        )
        await db.insert_followup(rec)
        out.append(rec)
    return out
