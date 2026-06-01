"""DocuSign envelope identity — operator reply-to overrides."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from propgen.config.loader import APIKeys, PropGenConfig
from propgen.sources import docusign as docusign_api


@pytest.mark.asyncio
async def test_envelope_uses_operator_reply_settings() -> None:
    config = PropGenConfig(
        operator_name="Pat Operator",
        operator_email="pat@example.com",
    )
    keys = APIKeys()
    keys.docusign_account_id = "acct-00000000-0000-0000-0000-000000000001"
    captured: dict = {}

    class FakeResponse:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"envelopeId": "env-test-1"}

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=FakeResponse())
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    async def capture_post(url: str, json=None, headers=None):  # noqa: ANN001
        captured["json"] = json
        return FakeResponse()

    mock_client.post = capture_post

    with (
        patch.object(docusign_api, "request_access_token", AsyncMock(return_value="token")),
        patch("propgen.sources.docusign.httpx.AsyncClient", return_value=mock_client),
    ):
        env_id = await docusign_api.send_envelope_for_pdf(
            config,
            keys,
            pdf_bytes=b"%PDF-1.4",
            document_name="proposal.pdf",
            signer_name="Signer",
            signer_email="signer@example.com",
            email_subject="Please sign",
            email_body="Body",
        )

    assert env_id == "env-test-1"
    settings = captured["json"]["emailSettings"]
    assert settings["replyEmailAddressOverride"] == "pat@example.com"
    assert settings["replyEmailNameOverride"] == "Pat Operator"
