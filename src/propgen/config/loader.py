# ruff: noqa: I001
"""PropGen configuration loader — YAML + .env, mirrors SchedBot shape."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

import yaml
from dotenv import load_dotenv
from pydantic import AliasChoices, BaseModel, Field

load_dotenv()


class AIConfig(BaseModel):
    model: str = "claude-sonnet-4-20250514"
    classifier_prompt_path: Optional[str] = None
    drafter_prompt_path: Optional[str] = None
    pricer_prompt_path: Optional[str] = None
    min_classification_confidence: float = 0.55
    min_pricing_confidence: float = 0.55
    max_narrative_chars: int = 8000


class BusinessBrandConfig(BaseModel):
    logo_path: str = "./assets/logo.png"
    primary_color: str = "#2A4A3B"
    secondary_color: str = "#C7B89C"
    legal_name: str = ""
    footer_text: str = ""


class BusinessConfig(BaseModel):
    name: str = "Your Business"
    business_type: str = ""
    timezone: str = "America/New_York"
    address: str = ""
    brand: BusinessBrandConfig = BusinessBrandConfig()
    communication_tone: str = "warm and professional"


class PricingConfig(BaseModel):
    currency: str = "USD"
    default_tax_rate: float = 0.0
    catalog: list[dict[str, Any]] = Field(default_factory=list)


class ProposalConfig(BaseModel):
    valid_for_days: int = 14
    require_approval: bool = True
    cover_email_subject_template: str = "Proposal: {{ subject }}"
    follow_up_cadence_days: list[int] = Field(default_factory=lambda: [3, 7, 14])
    auto_followup: bool = False
    accept_terms_md: str = "## Terms\n\nTBD."


class CrossEngineConfig(BaseModel):
    leadgen_db: Optional[str] = None
    schedbot_db: Optional[str] = None


class DocuSignConfig(BaseModel):
    base_url: str = "https://demo.docusign.net/restapi"
    oauth_host: str = "account-d.docusign.com"
    template_id: str = ""
    sign_here_anchor: str = "<<SIGN_HERE>>"
    webhook_secret: str = ""
    envelope_email_subject: str = "Please sign: {{ subject }}"
    envelope_email_body: str = (
        "Please review and sign the attached proposal.\n"
    )


class DropboxSignConfig(BaseModel):
    enabled: bool = False


class PandaDocConfig(BaseModel):
    enabled: bool = False


class WebhookConfig(BaseModel):
    enabled: bool = False
    hmac_header: str = "X-PropGen-Signature"
    expected_path: str = "/webhooks/proposal-request"


class EmailParserConfig(BaseModel):
    enabled: bool = False
    keywords_proposal: list[str] = Field(
        default_factory=lambda: ["proposal", "quote", "estimate", "scope", "pricing"]
    )


class OutreachConfig(BaseModel):
    require_approval: bool = True
    auto_send: bool = False
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_starttls: bool = True
    smtp_username: str = ""
    from_address: str = ""
    email_signature: str = "Best,\n{agent_name}\n{business_name}"
    daily_email_limit: int = 200


class SchedulerConfig(BaseModel):
    timezone: str = "America/New_York"
    business_hours_only: bool = True


class DatabaseConfig(BaseModel):
    sqlite_path: str = Field(
        default="./data/propgen.db",
        validation_alias=AliasChoices("sqlite_path", "path"),
    )
    pdf_dir: str = "./data/proposals"


class PropGenConfig(BaseModel):
    client_name: str = ""
    operator_name: str = "Operator"
    operator_title: str = ""
    operator_email: str = "ops@example.com"
    agent_name: str = ""
    agent_email: str = ""
    business: BusinessConfig = BusinessConfig()
    ai: AIConfig = AIConfig()
    pricing: PricingConfig = PricingConfig()
    proposal: ProposalConfig = ProposalConfig()
    cross_engine: CrossEngineConfig = CrossEngineConfig()
    docusign: DocuSignConfig = DocuSignConfig()
    dropbox_sign: DropboxSignConfig = DropboxSignConfig()
    pandadoc: PandaDocConfig = PandaDocConfig()
    webhook: WebhookConfig = WebhookConfig()
    email_parser: EmailParserConfig = EmailParserConfig()
    outreach: OutreachConfig = OutreachConfig()
    scheduler: SchedulerConfig = SchedulerConfig()
    database: DatabaseConfig = DatabaseConfig()


class APIKeys(BaseModel):
    anthropic: str = Field(default="", alias="ANTHROPIC_API_KEY")

    docusign_integration_key: str = Field(default="", alias="DOCUSIGN_INTEGRATION_KEY")
    docusign_user_id: str = Field(default="", alias="DOCUSIGN_USER_ID")
    docusign_account_id: str = Field(default="", alias="DOCUSIGN_ACCOUNT_ID")
    docusign_rsa_private_key_path: str = Field(
        default="", alias="DOCUSIGN_RSA_PRIVATE_KEY_PATH"
    )
    docusign_webhook_secret: str = Field(default="", alias="DOCUSIGN_WEBHOOK_SECRET")

    smtp_host: str = Field(default="", alias="SMTP_HOST")
    smtp_port: int = Field(default=587, alias="SMTP_PORT")
    smtp_username: str = Field(default="", alias="SMTP_USERNAME")
    smtp_password: str = Field(default="", alias="SMTP_PASSWORD")
    smtp_from_email: str = Field(default="", alias="SMTP_FROM_EMAIL")
    smtp_from_name: str = Field(default="", alias="SMTP_FROM_NAME")

    webhook_signing_secret: str = Field(default="", alias="WEBHOOK_SIGNING_SECRET")

    @classmethod
    def from_env(cls) -> APIKeys:
        values: dict[str, Any] = {}
        for field in cls.model_fields.values():
            alias = field.alias
            if not alias:
                continue
            raw = os.getenv(alias)
            if raw is None or raw == "":
                continue
            values[alias] = raw
        if "SMTP_PORT" in values and isinstance(values["SMTP_PORT"], str):
            values["SMTP_PORT"] = int(values["SMTP_PORT"])
        return cls(**values)


def display_agent_name(config: PropGenConfig) -> str:
    """Agent-facing label for logs and MCP metadata.

    Productized deployments (e.g. agentsia-core) set config.agent_name.
    Standalone PropGen installs fall back to the engine name.
    """
    name = (config.agent_name or "").strip()
    return name or "propgen"


def format_email_signature(config: PropGenConfig, template: str | None = None) -> str:
    """Render outreach/cover email signature (agent_name + business_name by default)."""
    tmpl = template if template is not None else config.outreach.email_signature
    if not tmpl:
        return ""
    return tmpl.format(
        agent_name=config.agent_name,
        agent_email=config.agent_email,
        business_name=config.business.name,
        operator_name=config.operator_name,
        operator_title=config.operator_title,
        operator_email=config.operator_email,
        client_name=config.client_name,
    ).strip()


def load_config(config_path: str | Path | None = None) -> PropGenConfig:
    path = Path(config_path or os.getenv("CONFIG_PATH", "config.yaml"))
    if not path.exists():
        raise FileNotFoundError(
            f"Config file not found: {path}\n"
            f"Copy config.example.yaml to {path} and fill in your details."
        )
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    config = PropGenConfig(**raw)

    # Anchor a relative SQLite path to the config file's directory rather than
    # the process working directory, so the DB lives in a stable location no
    # matter where the engine is launched from (Claude Desktop with no cwd, a
    # manual run from a parent repo, ...). An already-absolute path — e.g. one
    # the agentsia-core launcher pre-resolved — is left untouched.
    # (Note: `pdf_dir` is also a relative runtime path with the same cwd
    # sensitivity, but it is intentionally left to the launcher's chdir for now
    # since the shared agentsia-core anchor only pre-resolves the DB path.)
    sqlite_path = Path(config.database.sqlite_path)
    if not sqlite_path.is_absolute():
        config.database.sqlite_path = str((path.resolve().parent / sqlite_path).resolve())

    return config


def load_api_keys() -> APIKeys:
    keys = APIKeys.from_env()
    # Accept either env var name from DocuSign quickstarts / agentsia stubs.
    if not keys.docusign_rsa_private_key_path:
        alt = os.getenv("DOCUSIGN_PRIVATE_KEY_PATH", "")
        if alt:
            return keys.model_copy(update={"docusign_rsa_private_key_path": alt})
    return keys


def db_sqlite_path(config: PropGenConfig) -> str:
    return config.database.sqlite_path
