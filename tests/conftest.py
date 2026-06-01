"""Shared fixtures — generic placeholders only (no product persona names)."""

from __future__ import annotations

import pytest

from propgen.config.loader import APIKeys, BusinessConfig, PropGenConfig
from propgen.models import ClientInfo, LineItem, Proposal, ProposalVersion


@pytest.fixture
def test_config() -> PropGenConfig:
    return PropGenConfig(
        client_name="Example Co",
        operator_name="Pat Operator",
        operator_title="Principal Consultant",
        operator_email="pat@example.com",
        agent_name="Proposal Assistant",
        agent_email="assistant@example.com",
        business=BusinessConfig(name="Example Co"),
    )


@pytest.fixture
def test_keys() -> APIKeys:
    return APIKeys()


@pytest.fixture
def sample_proposal(test_config: PropGenConfig) -> Proposal:
    return Proposal(
        subject="Website redesign",
        client=ClientInfo(name="Acme", email="acme@example.com"),
        subtotal=1000.0,
        tax_amount=0.0,
        total=1000.0,
        currency=test_config.pricing.currency,
    )


@pytest.fixture
def sample_version(sample_proposal: Proposal) -> ProposalVersion:
    return ProposalVersion(
        proposal_id=sample_proposal.id,
        version_number=1,
        line_items=[
            LineItem(
                name="Consulting",
                quantity=1,
                unit_price=1000.0,
                line_total=1000.0,
            )
        ],
        narrative_md="## Scope\n\nExample scope paragraph.",
    )
