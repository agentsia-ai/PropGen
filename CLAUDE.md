# CLAUDE.md — PropGen

This file provides context and instructions for Claude (or any AI assistant)
working in this codebase. Read this before making any changes.

---

## What This Project Is

PropGen is a generic, AGPL-licensed, AI-powered proposal-and-quote
generation engine. It's designed to be the open-source core that any
operator can configure and deploy. Its job:

1. Ingest proposal requests from website forms (webhooks), inbound
   email, MCP tool calls, or by referencing a sibling LeadGen Lead /
   SchedBot Appointment id.
2. Classify each request against a small taxonomy: new proposal,
   revise existing, question, accepted, uncertain.
3. Draft scope, line items, narrative, and a cover note in the
   operator's voice — pricing the easy stuff from a configured catalog
   and asking a separate AI "pricing assistant" for ambiguous or custom
   scopes (with a confidence floor so junk never lands in a draft).
4. Render a branded PDF using ReportLab, with operator-supplied logo,
   color palette, header, and footer.
5. Push approved proposals to DocuSign for client signature; track
   sent / viewed / signed / declined / expired states via DocuSign
   Connect webhooks.
6. Draft polite follow-ups on a configurable cadence after a proposal
   is sent but unsigned — never auto-sending.
7. Handle acceptance — when a proposal is signed, mark it ACCEPTED,
   optionally hand off to SchedBot for kickoff scheduling, and draft a
   welcome / onboarding message.
8. Expose all functionality as an MCP server for conversational
   control via Claude Desktop — *never* autonomously sending.
9. Support productized deployments via subclassing or config-based
   prompt overrides (see *Customization Patterns* below).

This repository is intentionally **identity-free**. Anything specific to a
particular operator, brand, named agent, voice, or pricing philosophy
belongs in a downstream private repository or a local `config.yaml` —
never in this codebase.

---

## Architecture Overview

```
src/propgen/
├── _time.py                  # UTC-aware datetime helpers (now_utc, to_iso, parse_iso, format_local)
├── models.py                 # Proposal, ProposalVersion, LineItem, FollowUpRecord, AcceptanceEvent, RawProposalRequest
├── service.py                # High-level coordination (intake / draft / render / send / revise / pipeline)
├── cross_engine.py           # Read-only sibling-DB access (LeadGen / SchedBot SQLite by file path)
├── config/
│   └── loader.py             # Pydantic PropGenConfig + APIKeys
├── sources/                  # Inbound proposal-request connectors + e-sign integrations
│   ├── base.py               # ProposalSource + WebhookReceiver + ESignProvider ABCs
│   ├── docusign.py           # DocuSign REST (recommended primary e-sign)
│   ├── docusign_webhooks.py  # DocuSign Connect receiver (HMAC-verified)
│   ├── dropbox_sign.py       # Dropbox Sign — alternative (stub)
│   ├── pandadoc.py           # PandaDoc — alternative (stub)
│   ├── webhook.py            # Generic webhook receiver (website forms)
│   └── email_parser.py       # Parse proposal requests from inbound emails
├── ai/
│   ├── classifier.py         # RequestClassifier (pluggable)
│   ├── drafter.py            # ProposalDrafter (pluggable)
│   └── pricer.py             # PricingAssistant (pluggable, PropGen-specific)
├── pricing/                  # Deterministic pricing
│   └── catalog.py            # PricingCatalog — slug-keyed lookup + line-item math
├── pdf/                      # PDF rendering
│   └── renderer.py           # ReportLab-based proposal renderer
├── followup/
│   └── scheduler.py          # FollowUpScheduler — enqueue / draft drafts
├── crm/
│   └── database.py           # ProposalDatabase — async SQLite store
├── mcp_server/
│   └── server.py             # MCP server exposing all tools to Claude Desktop
└── cli.py                    # Click CLI entry point — `propgen ...`
```

### Data Flow

```
Source (webhook / email / lead_id / appt_id / manual) → RawProposalRequest
     → service.intake_proposal → Proposal (DRAFTED) + ProposalVersion v1 (DRAFTED)
     → RequestClassifier (only on email / uncertain inputs)
     → ProposalDrafter + PricingCatalog (+ optional PricingAssistant)
       → narrative_md + line_items_json populated on the version
     → renderer.render_proposal_pdf → PDF on disk; pdf_path stored
     → Operator approve (CLI or MCP) → confirmation_status=APPROVED
     → service.send_proposal → DocuSign envelope created (atomic) +
       SMTP cover note sent + Proposal.status=SENT + AcceptanceEvent(sent)
     → DocuSign Connect webhooks → AcceptanceEvent(viewed/signed/declined/voided) +
       Proposal.status updated atomically
     → Customer signs → service.handle_signed → status=ACCEPTED +
       optional SchedBot hand-off + welcome-note draft
     → Or: no response past follow_up_cadence_days → FollowUpScheduler
       drafts the next nudge (DRAFTED, awaiting approval)
     → Or: past expires_at → mark_expired → status=EXPIRED
```

Every transition is explicit. The engine will never collapse multiple
steps (e.g. "draft and send in one call" or "render and email in one
call") — that's the core guardrail.

---

## Key Design Principles

### 1. Never auto-send
`proposal.require_approval = true` and `proposal.auto_followup = false`
are the defaults and the only values the engine ships with verified.
The drafter output for cover notes and follow-ups is written to the row
in `DRAFTED` status; the DocuSign envelope is only created when the
operator's explicit approve flips the row. The MCP tool surface has no
"draft and send" combo. The only state-changing-+-outbound tool is
`send_proposal`, which requires the matching `approval_token`.

### 2. Never double-send
Send-side mutation is atomic. `ProposalDatabase.mark_sent` is a single
guarded UPDATE whose WHERE includes both a status check
(`status='drafted' OR status='ready_to_send'`) and the `approval_token`.
Concurrent CLI + MCP sends on the same proposal can never both succeed.
DocuSign envelope creation happens after the DB flip and is itself
idempotent on the proposal id (we tag the envelope with our id), so a
retry can't create two envelopes either.

### 3. Config-driven, not code-driven
Everything client-specific lives in `config.yaml` and `.env`. The engine
code should never contain hardcoded identity, brand, voice, prices, or
pricing philosophy. When adding features, ask: "should this be
configurable?" If yes, add it to the config schema in
`src/propgen/config/loader.py` first.

### 4. The data models are the contract
`src/propgen/models.py` defines `Proposal`, `ProposalVersion`,
`LineItem`, `PricingCatalog` (config), `FollowUpRecord`,
`AcceptanceEvent`, and the `RawProposalRequest` envelope. Every layer
(sources, AI, pricing, pdf, followup, CRM, outreach) speaks in these
objects. Never pass raw dicts between layers.

### 5. Async everywhere
All I/O is async (`httpx`, `aiosqlite`, `anthropic` async client). The
synchronous parts of the world we depend on (ReportLab, the DocuSign
SDK) are wrapped in `asyncio.to_thread` so they never block the event
loop. Don't introduce synchronous blocking calls in the hot path.

### 6. Timezone-aware everywhere
All datetimes are stored in UTC and presented in the configured
business timezone. The rule:
- Anything written to the DB goes through `to_iso(now_utc())` shape.
- Anything read from the DB comes back as a `tz-aware UTC` datetime via `parse_iso`.
- Anything shown to a human (PDF, CLI, cover-note email) goes through `format_local(dt, business.timezone)`.

There is no path through which a naive datetime should enter the
database. Don't add one.

### 7. Cross-engine reads are read-only by file path
PropGen can seed a proposal from a sibling LeadGen Lead or SchedBot
Appointment. **It does NOT import the leadgen or schedbot Python
packages.** `propgen.cross_engine` opens those SQLite files read-only
and runs hard-coded SELECT statements against the columns documented in
`docs/CROSS_ENGINE.md`. Any error (file missing, table missing, schema
drift, row missing) returns `None` so PropGen falls back to the
operator's explicit fields. This keeps PropGen deployable independently
of the other engines.

### 8. MCP tools must be self-describing
The MCP server is the primary operator interface. Tool names and
descriptions must be clear enough that Claude can reason about when to
use them without additional context. Keep tool schemas tight — prefer
fewer, well-named parameters over many optional ones.

### 9. White-label ready / identity-free
The engine code must not contain any operator- or client-specific
identity, brand name, named agent persona, voice, or pricing
philosophy. All identity flows in through `config.yaml` at runtime, or
through a downstream subclass that overrides the base classes (see
*Customization Patterns* below). The only exception is the LICENSE
copyright line, which is required by AGPL-3.0.

If you find yourself wanting to bake "Sage says..." or "Northstar
Design's blended rate is $X" into a prompt or default value here,
**stop** — that belongs in a downstream private repo, not in this
engine.

### 10. Engines stay public, personas live in downstream private repos
PropGen is published and forked as a standalone engine. Named personas
(e.g. Agentsia's Sage) live in a separate private repository and
consume PropGen as an installed dependency, subclassing the base
classes for voice and pricing. Do not accept PRs that add
persona-specific content to this repo.

---

## Customization Patterns

There are two supported ways to customize prompt behavior without
modifying this engine:

### Pattern A — Config-based prompt override (no code)

Point at external prompt files in your `config.yaml`:

```yaml
ai:
  model: "claude-sonnet-4-20250514"
  classifier_prompt_path: "./prompts/classifier.txt"
  drafter_prompt_path:    "./prompts/drafter.txt"
  pricer_prompt_path:     "./prompts/pricer.txt"
```

The base `RequestClassifier` / `ProposalDrafter` / `PricingAssistant`
will read these files at construction time and use them as the system
prompt. Missing files log a warning and fall back to the class-constant
default.

### Pattern B — Subclassing (for productized agents)

For named agents with personas (e.g. a downstream private repo defining
"Sage"), subclass and override the class constants:

```python
from propgen.ai.classifier import RequestClassifier
from propgen.ai.drafter import ProposalDrafter
from propgen.ai.pricer import PricingAssistant

class SageRequestClassifier(RequestClassifier):
    SYSTEM_PROMPT = "You are Sage's proposal-triage brain..."

class SageProposalDrafter(ProposalDrafter):
    SYSTEM_PROMPT = "You are Sage's voice — careful, generous..."

class SagePricingAssistant(PricingAssistant):
    SYSTEM_PROMPT = "You are Sage's pricing brain. Our blended rate is..."
```

The MCP server accepts `request_classifier_cls`, `proposal_drafter_cls`,
and `pricing_assistant_cls` kwargs on `main()` so an agent runtime can
inject these subclasses at startup. See `mcp_server/server.py`.

Both patterns can be combined (subclass for code-level customization,
then override per-deployment via config). This three-tier model
(engine → named agent → per-client config) is the canonical
productization shape.

---

## Working With This Codebase

### Running locally

```bash
# Install dependencies (uv-managed)
uv sync --extra dev

# Set up config
cp .env.example .env                       # fill in API keys
cp config.example.yaml config.yaml         # customize identity + pricing catalog

# Initialize database (safe to run anytime)
uv run propgen pipeline

# Create a proposal from explicit fields
uv run propgen create --client-email "jane@acme.example" \
                      --subject "Brand Identity Package" \
                      --line "brand_identity:1"

# Render the PDF
uv run propgen render <proposal-id>

# Review what's drafted
uv run propgen review

# Send (atomic; DocuSign envelope + cover email + status flip)
uv run propgen send <proposal-id>

# Start MCP server
uv run propgen mcp
```

### Adding a new e-sign provider

1. Create `src/propgen/sources/<provider>.py`
2. Subclass `ESignProvider` and implement `create_envelope()`,
   `void_envelope()`, and `fetch_envelope_status()`. Each method must
   return / raise the same shapes the DocuSign connector does so the
   service layer stays provider-neutral.
3. Add config fields to a new sub-model in
   `src/propgen/config/loader.py` and attach it to `PropGenConfig`.
4. Add any required env vars to `.env.example` and `APIKeys`.
5. Document the setup in `docs/API_KEYS.md`.
6. Wire provider selection into `service._build_esign_provider()`.

### Adding a new MCP tool

1. Add the `Tool` definition in `list_tools()` in
   `src/propgen/mcp_server/server.py`
2. Add the handler branch in `call_tool()`
3. Update `docs/MCP_SETUP.md` with the new tool in the tools table
4. Keep tool names snake_case and descriptions action-oriented

### Modifying the data models

- Add new fields with sensible defaults so existing DB rows don't break
- If a field needs fast filtering, denormalize it into a column in `crm/database.py`
- Update the row-to-model helpers (`_row_to_proposal`, `_row_to_version`,
  `_row_to_followup`, `_row_to_event`) in `crm/database.py` after
  adding persisted fields.

---

## Claude API Usage in This Project

PropGen uses Claude for three things:

### 1. Request Classification (`src/propgen/ai/classifier.py`)
- Default model: `claude-sonnet-4-20250514` (override via `ai.model` in config)
- Returns structured JSON
  `{"kind": "new_proposal|revise|question|accepted|uncertain", "confidence": 0.0, "reasoning": "..."}`
- Default prompt lives on `RequestClassifier.SYSTEM_PROMPT`
- Confidence below `ai.min_classification_confidence` is collapsed to
  `RequestKind.UNCERTAIN`

### 2. Proposal Drafting (`src/propgen/ai/drafter.py`)
- Default model: `claude-sonnet-4-20250514`
- Three methods, one per outbound surface:
  - `draft_proposal_narrative(proposal, version)` → markdown narrative
    (cover, scope, deliverables, timeline, terms summary)
  - `draft_cover_email(proposal, version)` → `(subject, body)` for the
    SMTP cover note that accompanies the DocuSign envelope
  - `draft_followup(proposal, version, prior_followups)` →
    `(subject, body)` for the next follow-up at the configured cadence
- Default prompt lives on `ProposalDrafter.SYSTEM_PROMPT`
- Hard-capped at `ai.max_narrative_chars` per section (default 6000)

### 3. Pricing Assistance (`src/propgen/ai/pricer.py`)
- Default model: `claude-sonnet-4-20250514`
- One method: `estimate_pricing(scope_text, catalog, ...)` —
  returns suggested `LineItem[]` plus a confidence and rationale.
- Default prompt lives on `PricingAssistant.SYSTEM_PROMPT`
- Confidence below `ai.min_pricing_confidence` is surfaced as "needs
  operator review" — the suggestion is returned but the service layer
  refuses to land it in a draft without an explicit approve.

### Prompt tuning tips
- Classifier quality improves when the prompt enumerates concrete
  edge cases (a "quick question" that's actually a pricing fishing
  expedition, a "revise" message that's really a request for a fresh
  proposal, etc.).
- Drafter quality improves when the operator's voice is described
  concretely ("warm, direct, never over-promises" beats "professional")
  and when the scope/inclusions/exclusions guidance is explicit.
- Pricing-assistant quality improves dramatically when the prompt
  includes the operator's blended rate, margin floor, and a few
  worked examples ("a 50-page web design at $1,200/page is $60,000;
  if the customer's budget is half that, drop scope, don't drop rate").
  This is the seam where margins live — tune the persona prompt, not
  the engine.
- Keep `max_tokens` tight — 400 for classify, 1200 for narrative,
  500 for cover, 400 for follow-up, 800 for pricing.

---

## MCP Server Ground Rules

The MCP server uses stdio transport. The stdio stream is the transport
for JSON-RPC frames — anything we write to it that isn't a JSON-RPC frame
shows up as "Unexpected token" errors in the Claude Desktop client.

Therefore, in any code path the MCP server can reach:

- **No `print()`** — ever. Use `logging` (stderr by default).
- **No `Console()` without `stderr=True`** — Rich's default console writes
  to stdout. Always construct `Console(stderr=True)` for MCP-facing output.
- **No config / credentials / DB loading at module import time** —
  initialize them inside `main()` after the caller has had a chance to
  `chdir` and set env vars. Use module-level `None` placeholders declared
  `global` inside `main()`.
- **Module-level pluggable class globals** —
  `REQUEST_CLASSIFIER_CLASS`, `PROPOSAL_DRAFTER_CLASS`,
  `PRICING_ASSISTANT_CLASS` default to the engine base classes.
  `main()` accepts `*_cls` kwargs and overwrites the globals before the
  server starts. This is the seam that productized agents plug their
  subclasses into.

See `src/propgen/mcp_server/server.py` for the reference implementation.

---

## Environment Variables

See `.env.example` for the full list. Required to run anything:
- `ANTHROPIC_API_KEY` — classification, drafting, pricing assistance

Required for the recommended (DocuSign) integration:
- `DOCUSIGN_INTEGRATION_KEY` — the integration key for your DocuSign app
- `DOCUSIGN_USER_ID` — the API username (a UUID, NOT your email) of the
  DocuSign user the JWT is impersonating
- `DOCUSIGN_ACCOUNT_ID` — the account id (UUID) of the DocuSign account
- `DOCUSIGN_PRIVATE_KEY_PATH` — path to the RSA private key whose public
  half is uploaded to DocuSign → Apps & Keys
- `DOCUSIGN_WEBHOOK_SECRET` — for HMAC-SHA256 verification on DocuSign
  Connect webhook payloads

Optional alternatives:
- `DROPBOX_SIGN_API_KEY` — Dropbox Sign API key
- `PANDADOC_API_KEY` — PandaDoc API key
- `SMTP_*` — for cover-note + follow-up sending
- `TWILIO_*` — for SMS follow-ups (install with `uv sync --extra sms`)
- `WEBHOOK_SIGNING_SECRET` — for the generic inbound webhook receiver

Never commit `.env` or `config.yaml` to git. Both are in `.gitignore`.

---

## Testing

```bash
# Run all tests
uv run pytest

# Run with coverage
uv run pytest --cov=src

# Run a specific test file
uv run pytest tests/test_pricing.py
```

Tests use `pytest-asyncio` for async test support. Mock external API
calls with `unittest.mock.AsyncMock` — never make real API calls
(Anthropic, DocuSign, SMTP) in tests. ReportLab calls are run for real
in unit tests but write to a tmpdir; the rendered PDFs are inspected
only for byte length and PDF magic-number sanity, never for visual
fidelity (that's a manual review concern).

---

## Common Gotchas

- **`propgen` command not found:** activate `.venv` or prefix with `uv run`.
- **DocuSign JWT auth fails with `consent_required`:** you have to grant
  one-time admin consent for your integration key. Visit the consent URL
  documented in `docs/API_KEYS.md` once per environment (demo /
  production), then retry.
- **DocuSign webhook signature failures:** confirm
  `DOCUSIGN_WEBHOOK_SECRET` matches the secret you set in DocuSign
  Connect, and confirm your edge proxy isn't rewriting the request body
  before signature verification — the HMAC is over the raw bytes.
- **MCP server must use stdio transport.** Don't switch to HTTP without
  updating Claude Desktop config.
- **Config reload:** config is loaded once at MCP server startup;
  restart the server after changing `config.yaml`.
- **PDF logo not appearing:** the `business.brand.logo_path` is
  resolved against the working directory at render time. When running
  via `agentsia sage ...`, the working directory is set to the agent
  dir (or the client dir for per-client deployments), so paths in the
  client config should be relative to that.
- **Cross-engine reads return None unexpectedly:** check the
  `cross_engine.*_db` paths actually point at existing files. PropGen
  intentionally never raises on cross-engine errors — it logs at INFO
  and returns None so the operator falls back to explicit fields.
- **`estimate_pricing` confidence is always low:** that's by design
  with the default prompt. Productize the `PricingAssistant` subclass
  with your operator's blended rate, margin floor, and a few worked
  examples to lift confidence.
- **Naive datetimes:** if you ever see a `datetime.utcnow()` call sneak
  in, that's a bug; use `now_utc()` from `_time.py`.

---

## Project Status

See `README.md` for the full feature overview. Current phase:
**v0.1.0 / initial scaffold**.

Stubs that still need implementation:
- `src/propgen/sources/dropbox_sign.py` — Dropbox Sign provider
  (skeleton ESignProvider; raises NotImplementedError on send-side ops).
- `src/propgen/sources/pandadoc.py` — PandaDoc provider (same shape).
- `src/propgen/sources/email_parser.py` — heuristic + AI parser is
  scaffolded; deployments wire it to a real inbox elsewhere.
- SMTP cover-note / follow-up sender layer — `OutreachConfig` knobs
  exist; the actual send-side worker is wired up in downstream
  personas for now (mirrors the SchedBot stance).

---

## License

AGPL-3.0. Same rules as every AGPL codebase: modifications served over
the network must be open-sourced under AGPL. See `LICENSE`.
