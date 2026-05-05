# PropGen

> An AI-powered proposal-and-quote generation engine for small-business operators. Turn a qualified lead or a confirmed scope conversation into a polished, signable proposal PDF — with a human-in-the-loop approval gate every step of the way.

---

## Vision

The hardest part of running a service business isn't writing the proposal — it's writing the *right* proposal, fast, in your own voice, and getting it in front of the prospect before the moment cools. Most proposal tools either stop at "fill in this template" (PandaDoc, Proposify) or balloon into full CPQ suites you have to learn (Salesforce CPQ, Conga). The actual labor — turning a discovery-call conversation into scope + line items + narrative + a price you'd actually charge, then chasing the signature — still falls on the operator.

**PropGen** sits on top of whatever e-sign tool you already use and does that labor:

- **AI-first** — Claude classifies inbound proposal requests, drafts scope / cover / follow-ups, and (separately) reasons about pricing for ambiguous or custom scopes.
- **E-sign-agnostic** — DocuSign is the recommended primary integration (JWT Grant flow, no per-user OAuth dance). Dropbox Sign and PandaDoc are stubbed for swap-in.
- **MCP-native** — runs as an MCP server so you can run the entire proposal desk conversationally from Claude Desktop.
- **Human-in-the-loop by default** — the engine *never* auto-sends. Every cover note, every follow-up, and every DocuSign envelope send requires an explicit approve step.
- **Atomic by construction** — the send_proposal path is a single guarded UPDATE that includes an approval-token interlock, so CLI and MCP can't race each other into a double-send.
- **Yours to white-label** — the engine is identity-free. Named personas (like Agentsia's Sage) live in downstream private repos as subclasses.

The goal: you spend 10 minutes a day approving AI-drafted proposals, revisions, and follow-ups. PropGen does the rest.

---

## Core Concepts

### The flow

```
┌──────────────┐  intake   ┌──────────────────┐   draft     ┌──────────────┐
│  webhook /   │──────────▶│ Proposal         │ ──────────▶ │ Proposal     │
│  email /     │           │ (DRAFTED)        │ + Pricing   │ + Version    │
│  lead_id /   │           └────────┬─────────┘ + Narrative │ (DRAFTED)    │
│  appt_id /   │                    │                       └──────┬───────┘
│  manual      │                    │                              │ render
└──────────────┘                    │                              ▼
                                    │                       ┌──────────────┐
                                    │                       │ PDF on disk  │
                                    │                       └──────┬───────┘
                                    │                              │ operator approves
                                    │                              ▼
                                    │                       ┌──────────────┐
                                    │                       │ DocuSign     │
                                    │                       │ envelope     │
                                    │                       │ created +    │
                                    │                       │ cover email  │
                                    │                       │ sent → SENT  │
                                    │                       └──────┬───────┘
                                    │                              │ webhooks
                                    │                              ▼
                                    │                       ┌──────────────┐
                                    │                       │ VIEWED →     │
                                    │                       │ SIGNED →     │
                                    │                       │ ACCEPTED     │
                                    │                       │ (or DECLINED │
                                    │                       │  / EXPIRED)  │
                                    │                       └──────────────┘
                                    │
                                    └─ revise → new ProposalVersion → re-draft → re-render
```

No step can be skipped. A proposal moves to `sent` only via the explicit approve → send path, with a DB-level token interlock that prevents double-sends across CLI and MCP.

### Three pluggable AI seams

Every class that talks to Claude is subclassable and prompt-overridable:

| Base class           | Where it lives                  | What it does                                                                              |
|----------------------|---------------------------------|-------------------------------------------------------------------------------------------|
| `RequestClassifier`  | `src/propgen/ai/classifier.py`  | Classifies an inbound request: new proposal / revise / question / accepted / uncertain    |
| `ProposalDrafter`    | `src/propgen/ai/drafter.py`     | Drafts cover notes, scope narratives, line-item descriptions, and follow-up messages      |
| `PricingAssistant`   | `src/propgen/ai/pricer.py`      | For ambiguous or custom scopes: suggests line items + a confidence and rationale          |

`PricingAssistant` is the PropGen-specific seam. The catalog handles the easy fixed-price line items; real proposals often need a judgment call ("this kitchen remodel is roughly 80–120 hours of skilled labor at our blended rate"). A persona's tuning of this seam is where margins, pricing philosophy, and risk tolerance live.

See [`CLAUDE.md`](./CLAUDE.md) → *Customization Patterns* for both customization paths (config-based prompt swap, or subclassing).

---

## Architecture

```
PropGen/
├── src/
│   └── propgen/                       # The package (standard Python src-layout)
│       ├── __init__.py
│       ├── cli.py                     # Click CLI entry point (`propgen ...`)
│       ├── mcp.py                     # `python -m propgen.mcp` MCP entry shim
│       ├── _time.py                   # UTC-aware datetime helpers
│       ├── models.py                  # Proposal, ProposalVersion, LineItem, FollowUpRecord, …
│       ├── service.py                 # High-level coordination (intake / draft / send / revise / pipeline)
│       ├── cross_engine.py            # Read-only sibling-DB access (LeadGen / SchedBot SQLite)
│       │
│       ├── ai/                        # Claude integration
│       │   ├── classifier.py          # RequestClassifier — pluggable
│       │   ├── drafter.py             # ProposalDrafter — pluggable
│       │   └── pricer.py              # PricingAssistant — pluggable
│       │
│       ├── config/
│       │   └── loader.py              # Pydantic PropGenConfig + APIKeys
│       │
│       ├── sources/                   # Proposal-request connectors (inbound) + e-sign integrations
│       │   ├── base.py                # ProposalSource + WebhookReceiver + ESignProvider ABCs
│       │   ├── docusign.py            # DocuSign REST (recommended primary e-sign)
│       │   ├── docusign_webhooks.py   # DocuSign Connect receiver + signature verify
│       │   ├── dropbox_sign.py        # Dropbox Sign (alternative — stub)
│       │   ├── pandadoc.py            # PandaDoc (alternative — stub)
│       │   ├── webhook.py             # Generic webhook receiver (website forms)
│       │   └── email_parser.py        # Parse proposal requests from emails
│       │
│       ├── pricing/                   # Deterministic pricing
│       │   └── catalog.py             # PricingCatalog — slug-keyed lookup + line-item math
│       │
│       ├── pdf/                       # PDF rendering
│       │   └── renderer.py            # ReportLab-based proposal renderer
│       │
│       ├── followup/                  # Follow-up cadence scheduling
│       │   └── scheduler.py           # FollowUpScheduler — enqueue / draft drafts
│       │
│       ├── crm/
│       │   └── database.py            # ProposalDatabase — async SQLite store
│       │
│       └── mcp_server/
│           └── server.py              # MCP server exposing all tools to Claude Desktop
│
├── docs/
│   ├── GETTING_STARTED.md
│   ├── API_KEYS.md                    # DocuSign JWT-grant + Connect setup walkthrough
│   ├── MCP_SETUP.md
│   ├── ARCHITECTURE.md
│   └── CROSS_ENGINE.md                # Documented LeadGen / SchedBot SQLite column contract
│
├── config.example.yaml                # Template config (copy → config.yaml)
├── pyproject.toml
├── .env.example
├── .gitignore
├── CLAUDE.md                          # AI-assistant context for this repo
└── LICENSE                            # AGPL-3.0
```

> **Customizing for a productized agent (named persona, tuned prompts)?**
> See [`CLAUDE.md`](./CLAUDE.md) → *Customization Patterns*. The base classes are
> designed to be subclassed or have their prompts swapped from a downstream repo.

---

## Quickstart

```bash
# 1. Clone
git clone https://github.com/agentsia-ai/PropGen.git
cd PropGen

# 2. Install (uv-managed; uv.lock pins exact deps)
uv sync --extra dev

# 3. Configure
cp .env.example .env                     # add ANTHROPIC + DOCUSIGN keys at minimum
cp config.example.yaml config.yaml       # customize identity + pricing catalog + brand

# 4. Initialize (creates the SQLite DB; safe to run anytime)
uv run propgen pipeline

# 5. Create a proposal from explicit fields
uv run propgen create --client-email "jane@acme.example" \
                      --subject "Brand Identity Package" \
                      --line "brand_identity:1"

# 6. Render the PDF for the current version
uv run propgen render <proposal-id>

# 7. Review the drafted cover note + envelope payload before sending
uv run propgen show <proposal-id>

# 8. Send (atomically: DocuSign envelope + cover email + status flip)
uv run propgen send <proposal-id>

# 9. See the pipeline grouped by status
uv run propgen pipeline

# 10. Start the MCP server (connect to Claude Desktop)
uv run propgen mcp
```

See [`docs/GETTING_STARTED.md`](./docs/GETTING_STARTED.md) for the full walkthrough, [`docs/API_KEYS.md`](./docs/API_KEYS.md) for the DocuSign JWT-grant + Connect setup, and [`docs/MCP_SETUP.md`](./docs/MCP_SETUP.md) for Claude Desktop setup.

---

## MCP Integration

PropGen exposes itself as an MCP server so you can run the proposal desk directly from Claude Desktop:

> "Draft a proposal for the discovery call we had with Acme Roofing on Tuesday — pull the scope from the SchedBot appointment and use our standard brand identity package + 8 hours of consulting."
>
> "The Northstar proposal still hasn't been opened — draft a polite nudge."
>
> "Acme just signed — mark it accepted, hand it off to SchedBot for kickoff scheduling, and draft a welcome note."

### Available MCP tools

| Tool                                | What it does                                                                |
|-------------------------------------|-----------------------------------------------------------------------------|
| `get_pipeline_summary`              | Counts by status (drafted / sent / viewed / signed / declined / expired)    |
| `create_proposal_from_lead`         | Pulls from LeadGen DB (read-only) + drafts a fresh proposal                 |
| `create_proposal_from_appointment`  | Pulls from SchedBot DB (read-only) + drafts a fresh proposal                |
| `create_proposal`                   | Explicit-fields form (no sibling lookup) — manual / CRM-importer flow      |
| `get_proposal_detail`               | Full detail on a proposal, all versions, all follow-ups                     |
| `list_proposals`                    | Filterable by status / date range                                           |
| `revise_proposal`                   | Produces a new ProposalVersion from a prior version + revision notes        |
| `render_proposal_pdf`               | Re-renders the current version's PDF                                        |
| `send_proposal`                     | Atomic: DocuSign envelope + cover email + status flip (approval-gated)      |
| `record_signed`                     | Manual override — e.g. wet signature, out-of-band acceptance                |
| `mark_declined`                     | Operator marks a proposal declined (with optional reason)                   |
| `mark_expired`                      | Operator marks an expired proposal — auto-fired by `propgen expire` too     |
| `draft_followup`                    | Drafts the next follow-up message at the configured cadence offset          |
| `list_followups`                    | Pending / drafted / sent follow-ups                                         |
| `get_pricing_catalog`               | Returns the configured pricing catalog                                      |
| `estimate_pricing`                  | Calls the AI pricing seam for a free-form scope; suggests line items        |

`send_proposal` is intentionally the only state-changing-+-outbound operation in one MCP call (mirroring SchedBot's `confirm_appointment`). It uses the same `approval_token` interlock — concurrent CLI + MCP sends can never both succeed.

---

## E-sign Provider Maturity

Not all e-sign providers are equally battle-tested. As of v0.1.0:

| Provider                  | Status              | Notes                                                                            |
|---------------------------|---------------------|----------------------------------------------------------------------------------|
| DocuSign REST + Connect   | Primary             | JWT Grant flow — the recommended quickstart path; HMAC-verified webhooks         |
| Dropbox Sign              | Stub                | Skeleton ESignProvider; wire it up for deployments already on Dropbox Sign       |
| PandaDoc                  | Stub                | Skeleton ESignProvider; wire it up for deployments already on PandaDoc           |
| Generic webhook receiver  | Supported           | For website forms / embedded proposal-request widgets; HMAC verification optional |
| Email parser              | Supported           | Heuristics + AI fallback; useful as a "plain-text request" fallback              |

If you're starting fresh: **use DocuSign**. The setup is one developer account plus a JWT-grant key pair, and DocuSign is ubiquitous in SMB and enterprise alike.

---

## Cross-engine reads

PropGen can seed a proposal from a sibling LeadGen or SchedBot database — pulling contact info, qualifier answers, and discovery-call notes — without taking a hard dependency on either engine. Cross-engine access is **read-only SQLite by file path**: PropGen does not import the leadgen or schedbot Python packages.

```yaml
cross_engine:
  leadgen_db: "../LeadGen/data/leadgen.db"     # null to disable
  schedbot_db: "../SchedBot/data/schedbot.db"  # null to disable
```

If a path is missing, the file doesn't exist, or the upstream schema has drifted, the cross-engine functions return `None` and the operator's explicit fields take over. See [`docs/CROSS_ENGINE.md`](./docs/CROSS_ENGINE.md) for the documented column contract.

---

## Safety / Guardrails

The engine ships locked down. A downstream deployment may relax some of these, but the defaults err toward humans:

- `proposal.require_approval = true` and `proposal.auto_followup = false` — the engine physically refuses to send anything without an explicit approve step.
- Approve → send interlock: each proposal has an `approval_token` checked at send time, so CLI and MCP can't race each other into a double-send.
- **Atomic envelope creation** — `send_proposal` is a single guarded UPDATE that flips `status='sent'` only when the approval token matches; concurrent sends on the same proposal can never both succeed.
- Confidence floor on the pricing assistant — anything below `min_pricing_confidence` is surfaced as "needs operator review" and never lands silently in a draft.
- Hard `max_narrative_chars` cap on generated narrative sections (default 6000).
- Default `ProposalDrafter` prompt forbids Claude from inventing prices, scope, or commitments not present in the request or operator notes.
- Default `PricingAssistant` prompt forbids it from putting a price on anything outside the catalog without raising a confidence flag.
- All datetimes are stored UTC and presented in `business.timezone` — there is no path for a naive datetime to enter the database.
- Cross-engine SQLite access is read-only and tolerates missing files / schema drift (returns `None`, never raises into the hot path).

---

## Productization / White-Label

PropGen is the open-source engine. Named personas (voice, tone, tuned prompts, pricing philosophy) live in downstream private repos as subclasses:

```python
from propgen.ai.drafter import ProposalDrafter
from propgen.ai.pricer import PricingAssistant

class MyBrandProposalDrafter(ProposalDrafter):
    SYSTEM_PROMPT = "You are MyBrand's proposal voice..."

class MyBrandPricingAssistant(PricingAssistant):
    SYSTEM_PROMPT = "You are MyBrand's pricing brain — our blended rate is..."
```

Those subclasses, plus a `config.yaml` pointing at them via the agent runtime, is the whole productization surface. See [`CLAUDE.md`](./CLAUDE.md).

---

## License

AGPL-3.0 — free to use, modify, and distribute. If you run a modified version as a network service, you must open-source your modifications under the same license. See [LICENSE](LICENSE) for full terms.

Copyright © Artificial Intelligentsia, LLC d/b/a Agentsia.

---

*A sibling engine to [LeadGen](https://github.com/agentsia-ai/LeadGen) (outbound), [CustComm](https://github.com/agentsia-ai/CustComm) (conversational), and [SchedBot](https://github.com/agentsia-ai/SchedBot) (calendar). Same architecture, different surface: PropGen runs the proposal desk.*
