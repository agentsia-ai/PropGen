# MCP Setup — Connecting PropGen to Claude Desktop

PropGen ships an MCP (Model Context Protocol) server so you can run the
entire proposal desk conversationally from Claude Desktop.

There are two ways to run it depending on whether you're using PropGen
standalone or as the engine for a productized agent like Sage.

---

## Option A: Standalone (PropGen as a generic engine)

For operators running the public PropGen engine on its own.

### Step 1: Install PropGen

```bash
git clone https://github.com/agentsia-ai/PropGen.git
cd PropGen
uv sync --extra dev                      # creates .venv and installs PropGen
cp .env.example .env                     # fill in Anthropic + DocuSign keys
cp config.example.yaml config.yaml       # customize identity + catalog
```

### Step 2: Configure Claude Desktop

Find your Claude Desktop config file:

| OS      | Path                                                                |
|---------|---------------------------------------------------------------------|
| macOS   | `~/Library/Application Support/Claude/claude_desktop_config.json`   |
| Windows | `%APPDATA%\Claude\claude_desktop_config.json`                       |

Add PropGen to the `mcpServers` section:

```json
{
  "mcpServers": {
    "propgen": {
      "command": "python",
      "args": ["-m", "propgen.mcp"],
      "cwd": "/absolute/path/to/your/PropGen"
    }
  }
}
```

On Windows, because Claude Desktop doesn't inherit your shell's PATH, it's
safest to point at the venv's absolute executable path directly:

```json
{
  "mcpServers": {
    "propgen": {
      "command": "C:\\path\\to\\your\\PropGen\\.venv\\Scripts\\propgen.exe",
      "args": ["mcp"],
      "cwd": "C:\\path\\to\\your\\PropGen"
    }
  }
}
```

The `cwd` matters: the MCP server resolves relative paths in
`config.yaml` (database, prompt overrides, DocuSign token cache, brand
logo) from this working directory.

---

## Option B: Productized agent (e.g. Sage via agentsia-core)

If you're running a named-persona deployment of PropGen, use the agent
runtime's CLI entry point instead. It injects the agent's tuned
`RequestClassifier` / `ProposalDrafter` / `PricingAssistant` subclasses
into the MCP server automatically.

For Sage (in the `agentsia-core` private repo):

```json
{
  "mcpServers": {
    "sage": {
      "command": "agentsia",
      "args": ["sage", "mcp"]
    }
  }
}
```

On Windows, again, point at the absolute exe path:

```json
{
  "mcpServers": {
    "sage": {
      "command": "C:\\path\\to\\your\\agentsia-core\\.venv\\Scripts\\agentsia.exe",
      "args": ["sage", "mcp"]
    }
  }
}
```

For per-client deployments of Sage:

```json
{
  "mcpServers": {
    "sage-greenleaf": {
      "command": "agentsia",
      "args": ["sage", "--client", "greenleaf_landscaping", "mcp"]
    }
  }
}
```

The `agentsia` CLI handles config layering, env loading, and
working-directory resolution itself — no `cwd` needed in the JSON.

> Heads up: Claude Desktop runs MCP commands without your shell's PATH
> on some systems. If `agentsia` isn't found, replace
> `"command": "agentsia"` with the absolute path output by
> `which agentsia` (or `where agentsia` on Windows).

---

## Step 3: Restart Claude Desktop

After saving the config, fully quit and relaunch Claude Desktop. You
should see a tools icon indicating MCP tools are available.

## Step 4: Talk to your pipeline

You can now say things like:

> "What's in the proposal pipeline this morning?"

> "A new request from Sam at Acme Co just landed in LeadGen as
> `lead-abc123`. Draft a proposal — they want a kitchen remodel sized
> around 80–120 hours of skilled labor."

> "The Acme proposal looks good. Approve it, then send it."

> "Smith asked us to drop the on-site discovery and bump the consulting
> hours from 10 to 16. Revise the proposal."

> "Mark the Doe proposal as declined — they went with another vendor."

> "Draft today's pending follow-ups so I can review and approve."

---

## Available MCP tools

| Tool                              | What it does                                                  |
|-----------------------------------|---------------------------------------------------------------|
| `get_pipeline_summary`            | Counts by status + open/won/lost rollups                      |
| `list_proposals`                  | List proposals (filterable by status / client / sent date)    |
| `get_proposal_detail`             | Full detail incl. versions, follow-ups, events                |
| `create_proposal_from_lead`       | Pull contact + intake from a sibling LeadGen Lead and draft   |
| `create_proposal_from_appointment`| Pull contact + intake from a sibling SchedBot Appointment     |
| `create_proposal`                 | Create from explicit fields (no sibling lookup)               |
| `revise_proposal`                 | Produce a new ProposalVersion off the latest                  |
| `render_proposal_pdf`             | Re-render the current version's PDF                           |
| `approve_proposal`                | DRAFTED → READY_TO_SEND (required for `send_proposal`)        |
| `send_proposal`                   | Atomic envelope + mark SENT + queue follow-ups (token-guarded)|
| `record_signed`                   | Manual override (wet sig / phone confirm) → SIGNED + ACCEPTED |
| `mark_declined`                   | Manual override: client declined out-of-band                  |
| `mark_expired`                    | Sweep SENT/VIEWED proposals past their valid_until            |
| `draft_followup`                  | Draft pending follow-ups missing a body                       |
| `list_followups`                  | All follow-ups for a proposal                                 |
| `get_pricing_catalog`             | Print the configured pricing catalog                          |
| `estimate_pricing`                | Ask the AI pricing assistant for line-item suggestions        |

`approve_proposal` and `send_proposal` are intentionally separate — the
MCP protocol must never allow a single conversational turn to both
approve and send a proposal. That's the core "no auto-send" guardrail
expressed at the tool surface.

---

## Troubleshooting

**Tools not showing up in Claude Desktop:**
- Confirm the `cwd` path is correct and absolute (Option A only).
- Confirm `propgen` / `agentsia` resolves on your PATH, or use the
  absolute executable path (see the Windows examples above).
- Check Claude Desktop logs:
  - macOS: `~/Library/Logs/Claude/`
  - Windows: `%APPDATA%\Claude\Logs\`

**"Unexpected token" or JSON parse warnings in the Claude Desktop log:**
- Something in the engine wrote non-JSON to stdout. PropGen routes all
  logs to stderr by design; if you see this after a code change, find
  the stray `print()` or default `Console()` and switch it to `logging`
  or `Console(stderr=True)`. See `CLAUDE.md` → *MCP Server Ground Rules*.

**`send_proposal` returns "Send refused":**
- The proposal isn't in `DRAFTED` or `READY_TO_SEND` status (it may
  already be `SENT`, `SIGNED`, or `DECLINED`).
- The proposal needs explicit approval first (`approve_proposal`) when
  `proposal.require_approval=true`.
- The `approval_token` argument doesn't match the proposal row. Pull
  the current token via `get_proposal_detail` and retry.

**DocuSign envelope create returns 401 / 403:**
- Run `propgen docusign ping` to confirm auth works at all.
- If `consent_required`, walk through the consent URL the error message
  prints (one-time per integration key).

**DocuSign Connect webhook signature failures:**
- `DOCUSIGN_WEBHOOK_SECRET` must exactly match the secret you set on
  the DocuSign Connect configuration.
- Some edge proxies rewrite or re-encode the JSON body before
  forwarding; verify the signature against the *raw* incoming bytes.
- Confirm DocuSign Connect is configured for **JSON** format (not XML).

**`estimate_pricing` returns confidence 0.0:**
- Confirm `ANTHROPIC_API_KEY` is set.
- The AI returned non-JSON. Run `propgen --debug ...` on a related CLI
  command to see raw Claude responses surfaced through the logger.

**Cross-engine reads return None:**
- Confirm `cross_engine.leadgen_db` / `cross_engine.schedbot_db` paths
  in `config.yaml` resolve to readable SQLite files.
- Confirm the sibling engine's schema matches the column contract in
  `docs/CROSS_ENGINE.md` (a major sibling upgrade may have drifted it).
