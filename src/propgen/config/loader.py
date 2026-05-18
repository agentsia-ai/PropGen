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
    email_signature: str = "Best,\n{operator_name}\n{business_name}"
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
    operator_name: str = "Operator"
    operator_email: str = "ops@example.com"
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


def load_config(config_path: str | Path | None = None) -> PropGenConfig:
    path = Path(config_path or os.getenv("CONFIG_PATH", "config.yaml"))
    if not path.exists():
        raise FileNotFoundError(
            f"Config file not found: {path}\n"
            f"Copy config.example.yaml to {path} and fill in your details."
        )
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return PropGenConfig(**raw)


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
