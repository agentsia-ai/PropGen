"""DocuSign REST client — JWT grant (server-to-server)."""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from pathlib import Path
from typing import Any

import httpx
import jwt

from propgen.config.loader import APIKeys, PropGenConfig

logger = logging.getLogger(__name__)

DOCUSIGN_SCOPES = "signature impersonation"


def _build_jwt_assertion(
    integration_key: str,
    user_id: str,
    private_key_pem: str,
    oauth_host: str,
) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "iss": integration_key,
            "sub": user_id,
            "aud": oauth_host,
            "iat": now,
            "exp": now + 3600,
            "scope": DOCUSIGN_SCOPES,
        },
        private_key_pem,
        algorithm="RS256",
    )


async def request_access_token(config: PropGenConfig, keys: APIKeys) -> str:
    if not keys.docusign_integration_key or not keys.docusign_user_id:
        raise ValueError("DocuSign JWT requires DOCUSIGN_INTEGRATION_KEY and DOCUSIGN_USER_ID")
    path = keys.docusign_rsa_private_key_path or ""
    if not path or not Path(path).is_file():
        raise ValueError("DOCUSIGN_RSA_PRIVATE_KEY_PATH must point at an RSA private key PEM file")
    pem = await asyncio.to_thread(Path(path).read_text, encoding="utf-8")
    oauth_host = config.docusign.oauth_host
    assertion = await asyncio.to_thread(
        _build_jwt_assertion,
        keys.docusign_integration_key,
        keys.docusign_user_id,
        pem,
        oauth_host,
    )
    token_url = f"https://{oauth_host}/oauth/token"
    data = {
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion": assertion,
    }
    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.post(token_url, data=data)
        r.raise_for_status()
        payload = r.json()
    access = payload.get("access_token")
    if not access:
        raise RuntimeError(f"DocuSign token response missing access_token: {payload}")
    return str(access)


async def docusign_ping(config: PropGenConfig, keys: APIKeys) -> dict[str, Any]:
    """Obtain an access token — verifies JWT configuration end-to-end."""
    token = await request_access_token(config, keys)
    return {"ok": True, "token_prefix": token[:12] + "..."}


async def send_envelope_for_pdf(
    config: PropGenConfig,
    keys: APIKeys,
    *,
    pdf_bytes: bytes,
    document_name: str,
    signer_name: str,
    signer_email: str,
    email_subject: str,
    email_body: str,
) -> str:
    token = await request_access_token(config, keys)
    account_id = keys.docusign_account_id
    if not account_id:
        raise ValueError("DOCUSIGN_ACCOUNT_ID is required to send envelopes")
    b64 = base64.b64encode(pdf_bytes).decode("ascii")
    anchor = config.docusign.sign_here_anchor
    body = {
        "emailSubject": email_subject,
        "emailBlurb": email_body,
        "documents": [
            {
                "documentBase64": b64,
                "name": document_name,
                "fileExtension": "pdf",
                "documentId": "1",
            }
        ],
        "recipients": {
            "signers": [
                {
                    "email": signer_email,
                    "name": signer_name,
                    "recipientId": "1",
                    "routingOrder": "1",
                    "tabs": {
                        "signHereTabs": [
                            {
                                "documentId": "1",
                                "anchorString": anchor,
                                "anchorUnits": "pixels",
                                "anchorXOffset": "0",
                                "anchorYOffset": "0",
                            }
                        ]
                    },
                }
            ]
        },
        "status": "sent",
    }
    url = f"{config.docusign.base_url.rstrip('/')}/v2.1/accounts/{account_id}/envelopes"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.post(url, json=body, headers=headers)
        if r.status_code >= 400:
            logger.error("DocuSign envelope error: %s %s", r.status_code, r.text)
        r.raise_for_status()
        data = r.json()
    eid = data.get("envelopeId")
    if not eid:
        raise RuntimeError(f"DocuSign missing envelopeId: {data}")
    return str(eid)
