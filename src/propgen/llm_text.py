"""Helpers for normalizing LLM output (fenced JSON, prose wrapped in JSON objects)."""

from __future__ import annotations

import json
import re
from typing import Any

_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*\n?(.*?)\n?```\s*$", re.DOTALL | re.IGNORECASE)


def strip_json_fence(text: str) -> str:
    t = text.strip()
    m = _JSON_FENCE_RE.match(t)
    return m.group(1).strip() if m else t


def parse_json_objectish(blob: str) -> dict[str, Any] | None:
    blob = strip_json_fence(blob)
    if not blob.startswith("{"):
        return None
    try:
        val = json.loads(blob)
    except json.JSONDecodeError:
        return None
    return val if isinstance(val, dict) else None


def unwrap_prose_maybe_json(
    text: str,
    *,
    content_keys: tuple[str, ...] = ("narrative_md", "body", "message", "text"),
) -> str:
    """If *text* is (optionally fenced) JSON with a prose field, return that prose; else *text*."""
    if not text or not str(text).strip():
        return text
    obj = parse_json_objectish(text)
    if obj is None:
        return text
    for k in content_keys:
        v = obj.get(k)
        if isinstance(v, str) and v.strip():
            return strip_json_fence(v).strip()
    return text


def normalize_proposal_draft_dict(raw: dict[str, Any]) -> dict[str, Any]:
    """Accept alternate key shapes (e.g. subject/body) from mis-aligned prompts."""
    out = dict(raw)
    nar = str(out.get("narrative_md") or "").strip()
    body = str(out.get("body") or "").strip()
    sub = str(out.get("subject") or "").strip()

    used_body_for_narrative = False
    if not nar and body and ("##" in body or body.startswith("#")):
        out["narrative_md"] = body
        used_body_for_narrative = True

    if sub and not str(out.get("cover_email_subject") or "").strip():
        out["cover_email_subject"] = sub

    cbody = str(out.get("cover_email_body") or "").strip()
    if not cbody and body and not used_body_for_narrative:
        out["cover_email_body"] = body
    return out
