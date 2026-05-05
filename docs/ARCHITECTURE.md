# PropGen Architecture

A short reference for contributors and for the downstream personas that
subclass these base classes (e.g. Agentsia's Sage).

---

## Data flow

```
┌──────────────┐  pull/push  ┌──────────────┐ ingest  ┌──────────────┐
│  webhook  /  │────────────▶│ RawProposal  │────────▶│ Proposal     │
│  email    /  │             │ Request      │ idempot │ (DRAFTED)    │
│  MCP tool /  │             └──────────────┘ on      │ + drafted    │
│  LeadGen  /  │                              ext_id  │   narrative  │
│  SchedBot /  │                                      │ + drafted    │
│  manual      │                                      │   cover      │
└──────────────┘                                      │ + rendered   │
                                                      │   PDF        │
                                                      └──────┬───────┘
                                                             │ approve (CLI/MCP)
                                                             ▼
                                                      ┌──────────────┐
                                                      │ READY_TO_SEND│
                                                      └──────┬───────┘
                                                             │ send (atomic + token)
                                                             ▼
                                                      ┌──────────────┐
                                                      │ SENT         │
                                                      │ (DocuSign    │
                                                      │  envelope)   │
                                                      │ + queued     │
                                                      │   follow-ups │
                                                      └──────┬───────┘
                                                             │ webhook event
                                                             ▼
                                                      ┌──────────────┐
                                                      │ VIEWED →     │
                                                      │ SIGNED →     │
                                                      │ ACCEPTED     │
                                                      │   ────       │
                                                      │ DECLINED /   │
                                                      │ EXPIRED /    │
                                                      │ VOIDED       │
                                                      └──────────────┘

Customer asks for changes ─▶ revise_proposal ─▶ new ProposalVersion + redraft
Customer signs offline    ─▶ record_signed   ─▶ SIGNED + ACCEPTED + welcome reply
Operator voids           ─▶ DocuSign void   ─▶ VOIDED
Past expires_at, unsigned ─▶ expire_overdue  ─▶ EXPIRED
```

Every transition is explicit and one-step. The engine has no "draft and
send in one call" path and no background autosend — sending is the only
state-change-plus-outbound MCP tool, and it requires an explicit
`approval_token`.

---

## Which base class touches what

| Stage                          | Class / module                              | Inputs                                  | Outputs                          |
|--------------------------------|---------------------------------------------|-----------------------------------------|----------------------------------|
| Inbound proposal-request       | `WebhookReceiver` subclass                  | HTTP body + headers                     | `RawProposalRequest`             |
| Inbound email parse            | `ProposalEmailParser`                       | `(sender, subject, body)`               | `RawProposalRequest`             |
| Sibling-engine seed            | `cross_engine.fetch_lead/appointment`       | sibling SQLite path + id                | `LeadSnapshot` / `AppointmentSnapshot` |
| Ingest into DB                 | `service.ingest_proposal_request`           | `RawProposalRequest` + DB               | `Proposal` (idempotent)          |
| Classify ambiguous request     | `RequestClassifier` **(pluggable)**         | inbound text                            | `ClassificationResult`           |
| Deterministic line-item pricing| `pricing.PricingCatalog`                    | catalog entry slug + qty                | `LineItem`                       |
| AI line-item suggestion        | `PricingAssistant` **(pluggable)**          | scope description + catalog             | `PricingSuggestion`              |
| Draft narrative + messages     | `ProposalDrafter` **(pluggable)**           | Proposal + version + line items         | `(narrative_md, subject, body)`  |
| Render PDF                     | `pdf.render_proposal_pdf`                   | Proposal + ProposalVersion + config     | PDF on disk                      |
| Atomic send-guard              | `ProposalDatabase.mark_sent`                | proposal_id + approval_token + envelope | bool (true iff this caller won)  |
| E-sign envelope create         | `ESignProvider.send_envelope`               | Proposal + version + PDF path           | `EnvelopeSendResult`             |
| E-sign event ingest            | `DocuSignWebhookReceiver` → `apply_envelope_event` | webhook payload                  | updated Proposal + AcceptanceEvent |
| Enqueue follow-ups             | `followup.FollowUpScheduler`                | Proposal + cadence config               | `FollowUpRecord` rows            |

The three pluggable base classes (`RequestClassifier`, `ProposalDrafter`,
`PricingAssistant`) are the only AI extension points a productized
persona needs. Every non-AI component (sources, pricing, PDF, follow-up,
DB, CLI, MCP server) is engine-owned and downstream deployments use it
unchanged.

---

## Database layout

Single SQLite file (default `./data/propgen.db`). Four tables:

- `proposals` — one row per proposal (every state)
- `proposal_versions` — one row per draft / revision (immutable history)
- `follow_ups` — one row per follow-up nudge (drafted / approved / sent / skipped)
- `acceptance_events` — append-only audit (sent / viewed / signed / declined / voided / expired / manual_override)

JSON blobs hold list-valued and nested fields (`tags_json`,
`raw_data_json`, `line_items_json`, `payload_json`). Denormalized columns
enable fast filtering:

- `proposals.status`, `proposals.client_email`, `proposals.sent_at`,
  `proposals.expires_at`, `proposals.docusign_envelope_id` (unique index
  for webhook idempotency)
- `proposal_versions.proposal_id`, `proposal_versions.version_number`
- `follow_ups.proposal_id`, `follow_ups.status`, `follow_ups.scheduled_for`
- `acceptance_events.proposal_id`, `acceptance_events.occurred_at`

---

## Two database interlocks

The two most dangerous code paths in a proposal engine are double-sending
and racing approve/send pairs. Both are guarded by single-statement
SQLite UPDATEs whose WHERE clauses include the safety checks.

### 1. Atomic send-guard (`mark_sent`)

```sql
UPDATE proposals
   SET status='sent',
       sent_at=?,
       expires_at=COALESCE(?, expires_at),
       docusign_envelope_id=COALESCE(?, docusign_envelope_id),
       updated_at=?
 WHERE id = ?
   AND status IN ('drafted', 'ready_to_send')
   AND approval_token = ?
```

Each proposal has an `approval_token` (uuid) generated at insert time.
Approving the proposal doesn't change the token. The send path requires
both `status IN ('drafted','ready_to_send')` AND the matching token, so
a CLI/MCP race or a retried send can't double-fire.

`send_proposal` orchestrates this with a "create envelope first, then
mark sent" order — if DocuSign errors, the proposal stays in
DRAFTED/READY_TO_SEND and is safe to retry. If `mark_sent` returns False
because somebody else already sent, we void the duplicate envelope so
the customer doesn't get two.

### 2. Follow-up send-guard (`mark_followup_sent`)

```sql
UPDATE follow_ups
   SET status='sent', sent_at=?, provider_message_id=?
 WHERE id = ?
   AND status = 'approved'
   AND approval_token = ?
```

Same pattern — approval bumps to `approved`, the token is required to
flip to `sent`. Belt-and-braces against background workers double-firing
the same nudge.

---

## Cross-engine reads (LeadGen / SchedBot)

PropGen does NOT import the leadgen or schedbot Python packages. Cross-
engine reads are by SQLite file path only, with hard-coded SELECT
statements against documented columns. See `docs/CROSS_ENGINE.md` for
the column contract.

Why: keeps PropGen deployable independently and avoids dependency hell
across engines. A schema drift in a sibling engine results in a logged
INFO and a `None` return — PropGen falls back to operator-supplied scope.

The sibling SQLite is opened in **immutable, read-only** mode
(`mode=ro&immutable=1`) so we sidestep any active write lock the sibling
engine might hold.

---

## MCP server lifecycle

```
                   ┌─────────────────────────────┐
                   │  agentsia sage mcp   OR     │
                   │  propgen mcp                │
                   │  OR python -m propgen.mcp   │
                   └──────────────┬──────────────┘
                                  │ imports
                                  ▼
                ┌────────────────────────────────────┐
                │  propgen.mcp_server.server.main()  │
                │                                    │
                │  1. apply *_cls kwargs →           │
                │     REQUEST_CLASSIFIER_CLASS, etc. │
                │  2. load_config() ← cwd now set    │
                │  3. load_api_keys()                │
                │  4. ProposalDatabase(...)          │
                │  5. stdio_server() run loop        │
                └──────────────┬─────────────────────┘
                               │ tool calls
                               ▼
                   ┌───────────────────────────┐
                   │ call_tool(name, args)     │
                   │   reads module globals:   │
                   │   config, keys, db,       │
                   │   *_CLASS constants       │
                   └───────────────────────────┘
```

Key property: steps 2–4 happen *inside* `main()`, not at module import.
This lets an outer caller (`agentsia-core`'s `AgentContext.activate()`)
`chdir` to a client-specific directory *before* the engine reads
`config.yaml` — so `./data/propgen.db` resolves to the client's DB and
not whatever happened to be cwd when Python first loaded the module.

Stdout is reserved for JSON-RPC frames. Everything else (banners,
progress, debug) goes to stderr via `logging` or a `Console(stderr=True)`.
Violating this shows up as "Unexpected token" errors in the Claude
Desktop log.

---

## PDF rendering

PropGen uses **ReportLab** (not WeasyPrint) for two reasons:

1. Pure-Python install — no Cairo/Pango/GDK system deps. Ships in any
   Lambda or Docker without a 200 MB image bloat. Works on Windows out
   of the box.
2. Programmatic API — keeps the renderer in lockstep with our Pydantic
   models without an HTML/CSS print-stylesheet impedance mismatch.

The synchronous ReportLab calls are wrapped in `asyncio.to_thread` so
the engine's all-async-IO contract holds.

Markdown narrative → flowables: a tiny `markdown-it-py` parse + visitor
that turns headings / paragraphs / lists / bold / italic into ReportLab
Paragraph runs. The drafter prompt constrains what it produces to a
supported subset.

---

## Time handling

PropGen is timezone-sensitive in the same way SchedBot is — every
proposal has a presentation timezone (`business.timezone`) that's
distinct from the canonical UTC instant we store.

The rule:
- **Store everything in UTC, always** — `now_utc()`, `to_iso()`, `parse_iso()`.
- **Display in the configured business timezone** — `to_local()`, `format_local()`.
- The DB schema only stores ISO strings; the row-to-model helpers in
  `crm/database.py` parse them back through `parse_iso()` so every
  Pydantic instance you handle is aware-UTC.

There is no path through which a naive datetime should enter the
database. If you ever see a `datetime.utcnow()` call sneak in, that's a
bug; use `now_utc()` from `_time.py`.

---

## Adding a persona subclass

A productized persona overrides only the three AI base classes, keeping
everything else default:

```python
# somewhere in agents/sage/drafter.py
from pathlib import Path
from propgen.ai.drafter import ProposalDrafter

_PROMPT_PATH = Path(__file__).parent / "prompts" / "drafter.txt"

class SageProposalDrafter(ProposalDrafter):
    SYSTEM_PROMPT = _PROMPT_PATH.read_text(encoding="utf-8")
```

The MCP server accepts `request_classifier_cls`, `proposal_drafter_cls`,
and `pricing_assistant_cls` kwargs on `main()`:

```python
from propgen.mcp_server.server import main as mcp_main
await mcp_main(
    request_classifier_cls=SageRequestClassifier,
    proposal_drafter_cls=SageProposalDrafter,
    pricing_assistant_cls=SagePricingAssistant,
)
```

The CLI path does not currently expose this injection because the
engine's own CLI is the generic path; productized CLIs like
`agentsia sage ...` do the injection themselves at the MCP layer.
