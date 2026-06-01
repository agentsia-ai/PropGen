"""Cover-email signature rendering — agent vs operator placeholders."""

from __future__ import annotations

from propgen.config.loader import BusinessConfig, OutreachConfig, PropGenConfig, format_email_signature


def test_cover_signature_uses_agent_name_not_operator() -> None:
    config = PropGenConfig(
        operator_name="Pat Operator",
        operator_title="Principal Consultant",
        operator_email="pat@example.com",
        agent_name="Proposal Assistant",
        agent_email="assistant@example.com",
        business=BusinessConfig(name="Example Co"),
        outreach=OutreachConfig(
            email_signature="Best,\n{agent_name}\n{business_name}",
        ),
    )
    sig = format_email_signature(config)
    assert "Proposal Assistant" in sig
    assert "Example Co" in sig
    assert "Pat Operator" not in sig
    assert "pat@example.com" not in sig


def test_signature_template_can_include_agent_email() -> None:
    config = PropGenConfig(
        agent_name="Proposal Assistant",
        agent_email="assistant@example.com",
        business=BusinessConfig(name="Example Co"),
        outreach=OutreachConfig(
            email_signature="{agent_name}\n{agent_email}",
        ),
    )
    sig = format_email_signature(config)
    assert sig == "Proposal Assistant\nassistant@example.com"
