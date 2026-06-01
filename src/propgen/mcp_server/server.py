"""MCP stdio server for PropGen."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from propgen.ai.classifier import RequestClassifier
from propgen.ai.drafter import ProposalDrafter
from propgen.ai.pricer import PricingAssistant
from propgen.config.loader import display_agent_name, db_sqlite_path, load_api_keys, load_config
from propgen.crm.database import ProposalDatabase
from propgen.models import ProposalStatus
from propgen.pricing.catalog import load_catalog_entries
from propgen.service import (
    approve_for_send,
    create_proposal_explicit,
    create_proposal_from_appointment,
    create_proposal_from_lead,
    draft_next_followup,
    mark_declined,
    record_signed_manual,
    render_current_pdf,
    resolve_proposal_id,
    revise_proposal,
    send_proposal,
    sweep_expired,
)

logger = logging.getLogger(__name__)


def _catalog_slug_pairs(raw: Any) -> list[tuple[str, float]]:
    """Normalize create_proposal catalog_slugs from MCP clients / LLMs."""
    pairs: list[tuple[str, float]] = []
    if not isinstance(raw, list):
        return pairs
    for entry in raw:
        if isinstance(entry, dict):
            slug = entry.get("slug") or entry.get("catalog_slug")
            if slug is None:
                continue
            try:
                pairs.append((str(slug), float(entry.get("quantity", 1))))
            except (TypeError, ValueError):
                continue
        elif isinstance(entry, (list, tuple)) and len(entry) >= 2:
            try:
                pairs.append((str(entry[0]), float(entry[1])))
            except (TypeError, ValueError):
                continue
    return pairs


app = Server("propgen")

config = None  # type: ignore[assignment]
keys = None  # type: ignore[assignment]
db = None  # type: ignore[assignment]

REQUEST_CLASSIFIER_CLASS: type[RequestClassifier] = RequestClassifier
PROPOSAL_DRAFTER_CLASS: type[ProposalDrafter] = ProposalDrafter
PRICING_ASSISTANT_CLASS: type[PricingAssistant] = PricingAssistant


def _json(obj: Any) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps(obj, indent=2, default=str))]


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="get_pipeline_summary",
            description="Counts proposals grouped by status (drafted, sent, viewed, ...).",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="create_proposal_from_lead",
            description="Seed a new proposal from a LeadGen SQLite row by lead id.",
            inputSchema={
                "type": "object",
                "properties": {"lead_id": {"type": "string"}},
                "required": ["lead_id"],
            },
        ),
        Tool(
            name="create_proposal_from_appointment",
            description="Seed a new proposal from a SchedBot SQLite appointment row.",
            inputSchema={
                "type": "object",
                "properties": {"appointment_id": {"type": "string"}},
                "required": ["appointment_id"],
            },
        ),
        Tool(
            name="create_proposal",
            description="Create a proposal from explicit client fields and catalog line items.",
            inputSchema={
                "type": "object",
                "properties": {
                    "client_name": {"type": "string"},
                    "client_email": {"type": "string"},
                    "client_phone": {"type": "string"},
                    "subject": {"type": "string"},
                    "catalog_slugs": {
                        "type": "array",
                        "description": (
                            "Catalog lines: objects {slug, quantity} or "
                            "{catalog_slug, quantity}; legacy [slug, quantity] pairs "
                            "still accepted when sent."
                        ),
                        "items": {
                            "anyOf": [
                                {
                                    "type": "object",
                                    "properties": {
                                        "slug": {"type": "string"},
                                        "quantity": {"type": "number"},
                                    },
                                    "required": ["slug", "quantity"],
                                },
                                {
                                    "type": "object",
                                    "properties": {
                                        "catalog_slug": {"type": "string"},
                                        "quantity": {"type": "number"},
                                    },
                                    "required": ["catalog_slug", "quantity"],
                                },
                                {
                                    "type": "array",
                                    "minItems": 2,
                                    "maxItems": 2,
                                    "prefixItems": [
                                        {"type": "string"},
                                        {"type": "number"},
                                    ],
                                },
                            ],
                        },
                    },
                },
                "required": ["client_email", "subject", "catalog_slugs"],
            },
        ),
        Tool(
            name="get_proposal_detail",
            description="Full proposal record with current version, follow-ups, events.",
            inputSchema={
                "type": "object",
                "properties": {"proposal_id": {"type": "string"}},
                "required": ["proposal_id"],
            },
        ),
        Tool(
            name="list_proposals",
            description="List proposals, optionally filtered by status.",
            inputSchema={
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "limit": {"type": "integer"},
                },
            },
        ),
        Tool(
            name="revise_proposal",
            description="Create a new ProposalVersion from the latest, re-drafted with guidance.",
            inputSchema={
                "type": "object",
                "properties": {
                    "proposal_id": {"type": "string"},
                    "guidance": {"type": "string"},
                },
                "required": ["proposal_id"],
            },
        ),
        Tool(
            name="render_proposal_pdf",
            description="Re-render the current version's PDF to disk.",
            inputSchema={
                "type": "object",
                "properties": {"proposal_id": {"type": "string"}},
                "required": ["proposal_id"],
            },
        ),
        Tool(
            name="send_proposal",
            description=(
                "Atomically: DocuSign envelope (sent) + cover email. Requires "
                "`approval_token` from the approve step when require_approval is true."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "proposal_id": {"type": "string"},
                    "approval_token": {"type": "string"},
                },
                "required": ["proposal_id", "approval_token"],
            },
        ),
        Tool(
            name="record_signed",
            description="Manual override — client signed offline.",
            inputSchema={
                "type": "object",
                "properties": {"proposal_id": {"type": "string"}},
                "required": ["proposal_id"],
            },
        ),
        Tool(
            name="mark_declined",
            description="Mark proposal DECLINED (out-of-band).",
            inputSchema={
                "type": "object",
                "properties": {"proposal_id": {"type": "string"}},
                "required": ["proposal_id"],
            },
        ),
        Tool(
            name="mark_expired",
            description="Sweep SENT/VIEWED proposals past expires_at to EXPIRED.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="draft_followup",
            description="Draft the next pending follow-up message (does not send).",
            inputSchema={
                "type": "object",
                "properties": {"proposal_id": {"type": "string"}},
                "required": ["proposal_id"],
            },
        ),
        Tool(
            name="list_followups",
            description="List follow-up records for a proposal.",
            inputSchema={
                "type": "object",
                "properties": {
                    "proposal_id": {"type": "string"},
                    "status": {"type": "string"},
                },
                "required": ["proposal_id"],
            },
        ),
        Tool(
            name="get_pricing_catalog",
            description="Return configured pricing catalog entries.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="estimate_pricing",
            description="AI-assisted line items + confidence from a free-form scope.",
            inputSchema={
                "type": "object",
                "properties": {"scope_description": {"type": "string"}},
                "required": ["scope_description"],
            },
        ),
        Tool(
            name="classify_proposal_request",
            description="Classify inbound text for proposal workflow intent.",
            inputSchema={
                "type": "object",
                "properties": {
                    "message_text": {"type": "string"},
                    "sender": {"type": "string"},
                    "subject": {"type": "string"},
                },
                "required": ["message_text"],
            },
        ),
        Tool(
            name="approve_proposal_send",
            description="Mark current version approved and return a one-time send approval_token.",
            inputSchema={
                "type": "object",
                "properties": {"proposal_id": {"type": "string"}},
                "required": ["proposal_id"],
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    await db.init()
    assert config is not None and keys is not None

    if name == "get_pipeline_summary":
        counts = await db.pipeline_counts()
        return _json({"agent": display_agent_name(config), "status_counts": counts})

    if name == "create_proposal_from_lead":
        result = await create_proposal_from_lead(
            config, keys, db, arguments["lead_id"], PROPOSAL_DRAFTER_CLASS
        )
        if not result:
            return _json({"ok": False, "error": "lead_not_found_or_cross_engine_disabled"})
        prop, ver = result
        return _json({"ok": True, "proposal_id": prop.id, "version_id": ver.id})

    if name == "create_proposal_from_appointment":
        result = await create_proposal_from_appointment(
            config, keys, db, arguments["appointment_id"], PROPOSAL_DRAFTER_CLASS
        )
        if not result:
            return _json({"ok": False, "error": "appointment_not_found_or_disabled"})
        prop, ver = result
        return _json({"ok": True, "proposal_id": prop.id, "version_id": ver.id})

    if name == "create_proposal":
        pairs = _catalog_slug_pairs(arguments.get("catalog_slugs") or [])
        prop, ver = await create_proposal_explicit(
            config,
            keys,
            db,
            client_name=arguments.get("client_name", ""),
            client_email=arguments["client_email"],
            client_phone=arguments.get("client_phone", ""),
            subject=arguments["subject"],
            catalog_slugs=pairs,
            drafter_cls=PROPOSAL_DRAFTER_CLASS,
        )
        return _json({"ok": True, "proposal_id": prop.id, "version_id": ver.id})

    if name == "get_proposal_detail":
        pid = await resolve_proposal_id(db, arguments["proposal_id"])
        if not pid:
            return _json({"error": "not_found"})
        prop = await db.get_proposal(pid)
        if not prop:
            return _json({"error": "not_found"})
        ver = await db.get_current_version(pid)
        evs = await db.list_acceptance_events(pid)
        fus = await db.list_followups(pid)
        return _json(
            {
                "proposal": prop.model_dump(mode="json"),
                "current_version": ver.model_dump(mode="json") if ver else None,
                "events": [e.model_dump(mode="json") for e in evs],
                "followups": [f.model_dump(mode="json") for f in fus],
            }
        )

    if name == "list_proposals":
        st = arguments.get("status")
        stat = ProposalStatus(st) if st else None
        rows = await db.list_proposals(status=stat, limit=int(arguments.get("limit", 50)))
        return _json([p.model_dump(mode="json") for p in rows])

    if name == "revise_proposal":
        pid = await resolve_proposal_id(db, arguments["proposal_id"])
        if not pid:
            return _json({"error": "not_found"})
        ver = await revise_proposal(
            config, keys, db, pid, arguments.get("guidance", ""), PROPOSAL_DRAFTER_CLASS
        )
        if not ver:
            return _json({"error": "revise_failed"})
        return _json({"ok": True, "version_id": ver.id})

    if name == "render_proposal_pdf":
        pid = await resolve_proposal_id(db, arguments["proposal_id"])
        if not pid:
            return _json({"error": "not_found"})
        path = await render_current_pdf(config, db, pid)
        return _json({"ok": True, "path": str(path) if path else None})

    if name == "send_proposal":
        pid = await resolve_proposal_id(db, arguments["proposal_id"])
        if not pid:
            return _json({"error": "not_found"})
        return _json(
            await send_proposal(config, keys, db, pid, arguments.get("approval_token", ""))
        )

    if name == "record_signed":
        pid = await resolve_proposal_id(db, arguments["proposal_id"])
        if not pid:
            return _json({"error": "not_found"})
        ok = await record_signed_manual(db, pid)
        return _json({"ok": ok})

    if name == "mark_declined":
        pid = await resolve_proposal_id(db, arguments["proposal_id"])
        if not pid:
            return _json({"error": "not_found"})
        ok = await mark_declined(db, pid)
        return _json({"ok": ok})

    if name == "mark_expired":
        n = await sweep_expired(config, db)
        return _json({"ok": True, "expired_count": n})

    if name == "draft_followup":
        pid = await resolve_proposal_id(db, arguments["proposal_id"])
        if not pid:
            return _json({"error": "not_found"})
        rec = await draft_next_followup(config, keys, db, pid, PROPOSAL_DRAFTER_CLASS)
        if not rec:
            return _json({"ok": False, "error": "no_pending_followup"})
        return _json({"ok": True, "followup": rec.model_dump(mode="json")})

    if name == "list_followups":
        from propgen.models import FollowUpStatus

        pid = await resolve_proposal_id(db, arguments["proposal_id"])
        if not pid:
            return _json({"error": "not_found"})
        st = arguments.get("status")
        st_enum = FollowUpStatus(st) if st else None
        fus = await db.list_followups(pid, status=st_enum)
        return _json([f.model_dump(mode="json") for f in fus])

    if name == "get_pricing_catalog":
        entries = load_catalog_entries(config)
        return _json([e.model_dump(mode="json") for e in entries])

    if name == "estimate_pricing":
        pricer = PRICING_ASSISTANT_CLASS(config, keys)
        result = await pricer.estimate_pricing(arguments["scope_description"])
        li = [x.model_dump(mode="json") for x in result.pop("line_items_models", [])]
        result["line_items"] = li
        return _json(result)

    if name == "classify_proposal_request":
        clf = REQUEST_CLASSIFIER_CLASS(config, keys)
        res = await clf.classify(
            arguments["message_text"],
            sender=arguments.get("sender", ""),
            subject=arguments.get("subject", ""),
        )
        return _json(res.model_dump(mode="json"))

    if name == "approve_proposal_send":
        pid = await resolve_proposal_id(db, arguments["proposal_id"])
        if not pid:
            return _json({"error": "not_found"})
        out = await approve_for_send(db, pid)
        if not out:
            return _json({"error": "approve_failed"})
        prop, tok = out
        return _json({"ok": True, "proposal_id": prop.id, "approval_token": tok})

    return _json({"error": f"unknown_tool:{name}"})


async def main(
    request_classifier_cls: type[RequestClassifier] | None = None,
    proposal_drafter_cls: type[ProposalDrafter] | None = None,
    pricing_assistant_cls: type[PricingAssistant] | None = None,
) -> None:
    global REQUEST_CLASSIFIER_CLASS, PROPOSAL_DRAFTER_CLASS, PRICING_ASSISTANT_CLASS
    global config, keys, db

    if request_classifier_cls is not None:
        REQUEST_CLASSIFIER_CLASS = request_classifier_cls
    if proposal_drafter_cls is not None:
        PROPOSAL_DRAFTER_CLASS = proposal_drafter_cls
    if pricing_assistant_cls is not None:
        PRICING_ASSISTANT_CLASS = pricing_assistant_cls

    config = load_config()
    keys = load_api_keys()
    db = ProposalDatabase(db_sqlite_path(config))

    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    agent_label = display_agent_name(config)
    logger.info("Starting PropGen MCP server (agent=%s)...", agent_label)
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
