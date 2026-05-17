"""AI pricing assistant — suggests line items from free-form scope."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import anthropic
from markdown_it import MarkdownIt

from propgen.config.loader import APIKeys, PropGenConfig
from propgen.models import BillingKind, LineItem, PricingCatalogEntry

logger = logging.getLogger(__name__)

DEFAULT_PRICER_PROMPT = """You help price small-business proposals using a catalog of SKUs.

You receive:
  - A scope description (may be markdown).
  - A JSON catalog of priced items (slug, name, unit_price, billing_kind, unit).

Return ONLY JSON:
{
  "line_items": [
    {"slug":"...","quantity":1.0,"rationale":"why this line"}
  ],
  "confidence": 0.0,
  "rationale": "overall sizing rationale"
}

Rules:
  - Prefer catalog slugs. If nothing fits, use slug "custom" with quantity 1 and explain in rationale (line_items may still reference custom as name in rationale only — for custom use the existing catalog slugs only; if impossible, pick closest catalog items and say so).
  - Never output a price not derived from catalog unit_price * quantity.
  - Be conservative — low confidence is better than false precision.
"""

_md = MarkdownIt("commonmark", {"html": False})


class PricingAssistant:
    SYSTEM_PROMPT: str = DEFAULT_PRICER_PROMPT

    def __init__(self, config: PropGenConfig, keys: APIKeys) -> None:
        self.config = config
        self.client = anthropic.AsyncAnthropic(api_key=keys.anthropic)
        self.model = config.ai.model
        self.min_confidence = config.ai.min_pricing_confidence
        self._system_prompt = self._load_system_prompt()

    def _load_system_prompt(self) -> str:
        override = self.config.ai.pricer_prompt_path
        if override:
            path = Path(override)
            if path.exists():
                return path.read_text(encoding="utf-8")
        return self.SYSTEM_PROMPT

    def _catalog_json(self) -> str:
        entries: list[PricingCatalogEntry] = []
        for raw in self.config.pricing.catalog:
            try:
                bk = BillingKind(str(raw.get("billing_kind", "fixed")))
            except ValueError:
                bk = BillingKind.FIXED
            entries.append(
                PricingCatalogEntry(
                    slug=str(raw["slug"]),
                    name=str(raw.get("name", raw["slug"])),
                    description=str(raw.get("description", "")),
                    unit_price=float(raw.get("unit_price", 0)),
                    unit=str(raw.get("unit", "ea")),
                    billing_kind=bk,
                    taxable=bool(raw.get("taxable", True)),
                )
            )
        return json.dumps([e.model_dump(mode="json") for e in entries], indent=2)

    async def estimate_pricing(self, scope_description: str) -> dict:
        """Returns dict with line_items: list[LineItem], confidence, rationale."""
        plain = _md.render(scope_description)
        user = f"""Catalog:
{self._catalog_json()}

Scope (HTML-stripped markdown render for hints — prices only from catalog):
{plain[:12000]}
"""
        msg = await self.client.messages.create(
            model=self.model,
            max_tokens=1500,
            system=self._system_prompt,
            messages=[{"role": "user", "content": user}],
        )
        block = msg.content[0]
        text = block.text if hasattr(block, "text") else str(block)
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return {"line_items": [], "confidence": 0.0, "rationale": "unparseable_model_output"}
        cat = {str(r["slug"]): r for r in self.config.pricing.catalog}
        items: list[LineItem] = []
        conf = float(data.get("confidence", 0))
        for i, row in enumerate(data.get("line_items") or []):
            raw_slug = row.get("slug", row.get("catalog_slug", ""))
            slug = str(raw_slug or "")
            qty = float(row.get("quantity", 1))
            if slug not in cat:
                continue
            entry = cat[slug]
            up = float(entry.get("unit_price", 0))
            desc = row.get("rationale", row.get("description", ""))
            items.append(
                LineItem(
                    name=str(entry.get("name", slug)),
                    description=str(desc or ""),
                    quantity=qty,
                    unit=str(entry.get("unit", "ea")),
                    unit_price=up,
                    line_total=qty * up,
                    taxable=bool(entry.get("taxable", True)),
                    sort_order=i,
                )
            )
        if conf < self.min_confidence:
            data["low_confidence_flag"] = True
        data["line_items_models"] = items
        data["rationale"] = str(data.get("rationale", ""))
        data["confidence"] = conf
        return data
