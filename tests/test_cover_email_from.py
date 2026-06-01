"""Cover-note SMTP From address — agent_email when configured."""

from __future__ import annotations

from propgen.config.loader import APIKeys, PropGenConfig
from propgen.service import _cover_email_from_address


def test_cover_from_prefers_agent_email() -> None:
    config = PropGenConfig(
        operator_email="pat@example.com",
        agent_email="assistant@example.com",
    )
    keys = APIKeys()
    assert _cover_email_from_address(config, keys) == "assistant@example.com"


def test_cover_from_falls_back_to_operator_when_no_agent_email() -> None:
    config = PropGenConfig(operator_email="pat@example.com", agent_email="")
    keys = APIKeys()
    assert _cover_email_from_address(config, keys) == "pat@example.com"
