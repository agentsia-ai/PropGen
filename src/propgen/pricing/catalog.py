"""Deterministic pricing from the configured catalog."""

from __future__ import annotations

from propgen.config.loader import PropGenConfig
from propgen.models import BillingKind, LineItem, PricingCatalogEntry


def load_catalog_entries(config: PropGenConfig) -> list[PricingCatalogEntry]:
    out: list[PricingCatalogEntry] = []
    for raw in config.pricing.catalog:
        bk = raw.get("billing_kind", "fixed")
        try:
            kind = BillingKind(bk)
        except ValueError:
            kind = BillingKind.FIXED
        out.append(
            PricingCatalogEntry(
                slug=str(raw["slug"]),
                name=str(raw.get("name", raw["slug"])),
                description=str(raw.get("description", "")),
                unit_price=float(raw.get("unit_price", 0)),
                unit=str(raw.get("unit", "ea")),
                billing_kind=kind,
                taxable=bool(raw.get("taxable", True)),
            )
        )
    return out


def catalog_by_slug(config: PropGenConfig) -> dict[str, PricingCatalogEntry]:
    return {e.slug: e for e in load_catalog_entries(config)}


def line_items_from_slugs(
    config: PropGenConfig,
    slugs: list[tuple[str, float]],
) -> list[LineItem]:
    """Build line items from (slug, quantity) pairs."""
    cat = catalog_by_slug(config)
    items: list[LineItem] = []
    for i, (slug, qty) in enumerate(slugs):
        e = cat.get(slug)
        if not e:
            continue
        total = float(qty) * float(e.unit_price)
        items.append(
            LineItem(
                name=e.name,
                description=e.description,
                quantity=float(qty),
                unit=e.unit,
                unit_price=float(e.unit_price),
                line_total=total,
                taxable=e.taxable,
                sort_order=i,
            )
        )
    return items


def compute_totals(
    items: list[LineItem],
    currency: str,
    tax_rate: float,
    discount: float = 0.0,
) -> tuple[float, float, float, float]:
    subtotal = sum(li.line_total for li in items if not li.optional)
    taxable_base = sum(li.line_total for li in items if li.taxable and not li.optional)
    tax_amount = taxable_base * float(tax_rate)
    total = subtotal + tax_amount - discount
    return subtotal, tax_amount, discount, total
