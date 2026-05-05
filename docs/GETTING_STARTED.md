# Getting Started with PropGen

This guide takes you from a fresh clone to your first AI-drafted
proposal PDF, sent for signature via DocuSign. Should take about 45
minutes (most of which is the DocuSign developer-account setup).

---

## Prerequisites

- Python 3.12+
- `uv` package manager — install at https://docs.astral.sh/uv/
- An Anthropic API key (free credits available at https://console.anthropic.com)
- A DocuSign developer account (free; the recommended primary integration)

---

## Step 1 — Clone the Repo

```bash
git clone https://github.com/agentsia-ai/PropGen.git
cd PropGen
```

---

## Step 2 — Install Dependencies

PropGen uses `uv` for fast, reliable dependency management. **Do not mix
in raw `pip install` — it will diverge from `uv.lock`.**

```bash
# Install uv if you haven't already
curl -LsSf https://astral.sh/uv/install.sh | sh

# Create the venv and install all dependencies (incl. dev tools)
uv sync --extra dev
```

After this, the `propgen` command is available inside `.venv`.

To activate the environment for the session:
```bash
# macOS / Linux
source .venv/bin/activate

# Windows (PowerShell)
.venv\Scripts\Activate.ps1
```

Or prefix all commands with `uv run`:
```bash
uv run propgen pipeline
```

---

## Step 3 — Get Your API Keys

### Anthropic (required)
1. Go to https://console.anthropic.com → sign up
2. API Keys → Create Key
3. You get $5 in free credits — plenty for early testing

### DocuSign (recommended primary)
The full walk-through is in `docs/API_KEYS.md`. The short version:
1. Sign up at https://developers.docusign.com
2. Apps and Keys → Add App → enable Authorization Code Grant + JWT Grant
3. Generate an RSA keypair, save the private key locally
4. Find your Integration Key, User ID, Account ID
5. Grant one-time consent in a browser
6. `propgen docusign ping` round-trips to confirm

---

## Step 4 — Configure Your Environment

```bash
cp .env.example .env
```

Open `.env` and fill in at minimum:
```
ANTHROPIC_API_KEY=sk-ant-...
DOCUSIGN_INTEGRATION_KEY=00000000-0000-0000-0000-000000000000
DOCUSIGN_USER_ID=00000000-0000-0000-0000-000000000000
DOCUSIGN_ACCOUNT_ID=00000000-0000-0000-0000-000000000000
DOCUSIGN_PRIVATE_KEY_PATH=./docusign_private.key
DOCUSIGN_WEBHOOK_SECRET=    # leave blank if you haven't set up Connect yet
```

Everything else can stay at its default for now.

---

## Step 5 — Configure PropGen

```bash
cp config.example.yaml config.yaml
```

Open `config.yaml` and set the identity + business fields at the top:

```yaml
operator_name: "Your Name"
operator_email: "you@example.com"

business:
  name: "Your Business Name"
  business_type: "your industry"
  timezone: "America/New_York"
  brand:
    logo_path: "./assets/logo.png"
    primary_color: "#2A4A3B"
    secondary_color: "#C7B89C"
    legal_name: "Your Legal Entity Name LLC"
    footer_text: "youdomain.com  ·  hello@yourdomain.com"
```

The single most important section is `pricing.catalog` — the priced
offerings you sell. The deterministic side of pricing keys off these
slugs.

```yaml
pricing:
  currency: "USD"
  default_tax_rate: 0.0
  catalog:
    - slug: "discovery_call"
      name: "Discovery Call"
      unit_price: 0
      billing_kind: "fixed"
      taxable: false
    - slug: "consulting_hour"
      name: "Senior Consulting"
      unit_price: 250
      billing_kind: "hourly"
      taxable: true
```

Take a moment on `proposal.accept_terms_md` once you're sending live
proposals — that markdown gets appended verbatim to every PDF.

---

## Step 6 — Initialize the Database

```bash
uv run propgen pipeline
```

This creates `./data/propgen.db` and prints an (empty) pipeline summary:

```
┌────────┬───────┐
│ Status │ Count │
├────────┼───────┤
│ TOTAL  │ 0     │
└────────┴───────┘

  open: 0   won: 0   lost: 0
```

---

## Step 7 — Verify DocuSign

```bash
uv run propgen docusign ping
```

You should see:

```
OK DocuSign auth round-trip succeeded.
  user: you@example.com  (00000000-...)  name: Your Name
  account: 00000000-...  ★  name=Your Account  base_uri=https://demo.docusign.net
```

If you see `consent_required`, follow the URL the error prints, click
**Allow Access**, and retry.

---

## Step 8 — Create Your First Proposal

For a quick end-to-end test, create a proposal from explicit fields:

```bash
uv run propgen mcp
```

…then ask Claude Desktop (with PropGen wired up per `docs/MCP_SETUP.md`):

> "Create a proposal for jane@example.com from Acme Co. Subject is
> 'Quarterly consulting engagement'. They want 10 hours of senior
> consulting and one discovery call."

Or, from the CLI (using your real catalog slugs):

```bash
# Drop into a Python REPL inside the venv:
uv run python -c "
import asyncio
from propgen.config.loader import load_api_keys, load_config
from propgen.crm.database import ProposalDatabase
from propgen.models import ClientInfo
from propgen.service import create_proposal

async def main():
    config = load_config()
    keys = load_api_keys()
    db = ProposalDatabase(config.database.sqlite_path)
    await db.init()
    p = await create_proposal(
        config, keys, db,
        client=ClientInfo(name='Jane Doe', email='jane@example.com', company='Acme Co'),
        subject='Quarterly consulting engagement',
        intake_notes='10 hours of senior consulting + 1 discovery call.',
        catalog_slugs=[('consulting_hour', 10), ('discovery_call', 1)],
    )
    print(f'Created {p.id} — total {p.currency} {p.total:.2f}')
    print(f'Approval token: {p.approval_token}')

asyncio.run(main())
"
```

After this, the proposal is `DRAFTED` and the PDF lives at
`./data/proposals/<id>-v1.pdf`. Open it.

---

## Step 9 — Approve and Send

```bash
# DRAFTED → READY_TO_SEND
uv run propgen approve <proposal-id>

# Push the envelope to DocuSign and mark SENT
uv run propgen send <proposal-id>
```

`send` requires the proposal's `approval_token`. By default the CLI
will pull it off the row, but you can pass `--token <uuid>` explicitly
to be airtight against staleness.

The customer (in this case the email you used) will get a DocuSign email
with the rendered PDF attached and a SignHere tab anchored on the
signature block.

---

## Step 10 — See the Pipeline

```bash
uv run propgen pipeline
uv run propgen list --status sent
uv run propgen show <proposal-id>
```

`show` prints the full proposal: status, line items, narrative, follow-up
queue, and the audit-event timeline.

---

## Step 11 — Revise

If the customer asks for changes:

```bash
uv run propgen revise <proposal-id> \
    --notes "Drop the discovery call; bump consulting from 10h to 16h" \
    --catalog consulting_hour:16
```

A new ProposalVersion is inserted, the PDF is re-rendered, the cover
note is re-drafted with a "this is a revision" framing, and (if the
proposal had been SENT) the status drops back to DRAFTED for re-approval.

---

## Step 12 — Follow-ups

Three days after a proposal is SENT, a follow-up record will be queued
(per `proposal.follow_up_cadence_days`). Draft them:

```bash
uv run propgen draft-followups
```

Each follow-up is in DRAFTED status with a populated subject + body,
waiting for operator approval. The engine never auto-sends.

---

## Step 13 — Cross-engine seeding (optional)

If you also run LeadGen or SchedBot, point PropGen at their SQLite
files in `config.yaml`:

```yaml
cross_engine:
  leadgen_db: "../LeadGen/data/leadgen.db"
  schedbot_db: "../SchedBot/data/schedbot.db"
```

Then:

```bash
uv run propgen from-lead lead-abc123 --use-pricer
uv run propgen from-appointment appt-def456 --catalog consulting_hour:8
```

PropGen reads sibling rows read-only by file path — no Python-package
import. See `docs/CROSS_ENGINE.md` for the column contract.

---

## Step 14 — Connect to Claude Desktop (Recommended)

PropGen's MCP server is the most pleasant way to operate day-to-day —
Claude can run all of the above tools conversationally. Start it:

```bash
uv run propgen mcp
```

Then see `docs/MCP_SETUP.md` for the Claude Desktop configuration block.

---

## Common Issues

**`propgen: command not found`**
→ Your virtual environment isn't activated. Run `source .venv/bin/activate`
  (or `.venv\Scripts\Activate.ps1` on Windows), or prefix commands with
  `uv run`.

**`Config file not found: config.yaml`**
→ Run `cp config.example.yaml config.yaml` and edit the identity fields.

**`ANTHROPIC_API_KEY is not set`**
→ Your `.env` file is missing or the key isn't set correctly.

**`DOCUSIGN_INTEGRATION_KEY and DOCUSIGN_USER_ID must be set`**
→ Walk through `docs/API_KEYS.md` → DocuSign Step 4.

**DocuSign returns `consent_required`**
→ Open the URL the error prints in a browser, sign in as the API user,
  click **Allow Access**, retry.

**PDF logo doesn't appear**
→ Confirm `business.brand.logo_path` resolves from the working directory
  PropGen is run from. Use an absolute path if you're calling PropGen
  from a different cwd.

**MCP server prints "Unexpected token" warnings in Claude Desktop**
→ Something in the engine is writing to stdout. Every new code path
  reachable from `mcp_server/server.py` must use `logging` or
  `Console(stderr=True)`, not `print` or a default `Console()`. See
  `CLAUDE.md` → *MCP Server Ground Rules*.

---

## Next Steps

- Read `docs/ARCHITECTURE.md` for the deep dive on how everything fits
  together
- Read `docs/API_KEYS.md` for the full DocuSign setup walk-through, plus
  alternative providers (Dropbox Sign / PandaDoc) and SMTP / Twilio
- Read `docs/MCP_SETUP.md` to wire PropGen into Claude Desktop
- Read `docs/CROSS_ENGINE.md` to understand the LeadGen / SchedBot DB
  column contract PropGen reads
- Read `CLAUDE.md` → *Customization Patterns* when you're ready to plug
  in a custom voice or persona
