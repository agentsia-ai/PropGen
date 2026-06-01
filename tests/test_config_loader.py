"""Config loader tests — operator vs agent identity fields."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from propgen.config.loader import PropGenConfig, display_agent_name, load_config


def test_load_config_reads_identity_fields(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        yaml.safe_dump(
            {
                "client_name": "Example Co",
                "operator_name": "Pat Operator",
                "operator_title": "Principal Consultant",
                "operator_email": "pat@example.com",
                "agent_name": "Proposal Assistant",
                "agent_email": "assistant@example.com",
            }
        ),
        encoding="utf-8",
    )
    cfg = load_config(cfg_path)
    assert cfg.client_name == "Example Co"
    assert cfg.operator_name == "Pat Operator"
    assert cfg.operator_title == "Principal Consultant"
    assert cfg.operator_email == "pat@example.com"
    assert cfg.agent_name == "Proposal Assistant"
    assert cfg.agent_email == "assistant@example.com"
    assert cfg.proposal.require_approval is True


def test_display_agent_name_falls_back_to_engine_name() -> None:
    cfg = PropGenConfig(
        operator_name="Pat Operator",
        operator_email="pat@example.com",
    )
    assert display_agent_name(cfg) == "propgen"
    cfg.agent_name = "Proposal Assistant"
    assert display_agent_name(cfg) == "Proposal Assistant"


def test_load_config_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "nope.yaml")
