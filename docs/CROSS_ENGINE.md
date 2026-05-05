# Cross-Engine Reads — LeadGen / SchedBot Column Contract

PropGen seeds proposals from a sibling LeadGen `Lead` or SchedBot
`Appointment`. To keep the engines deployable independently and avoid
dependency hell, PropGen does NOT import the leadgen or schedbot Python
packages. Instead, it reads the sibling SQLite files **read-only, by
file path**, with hard-coded SELECT statements.

This document is the contract: which columns PropGen reads, what it
expects them to mean, and how it degrades when the sibling schema
drifts.

---

## How it works

```yaml
# config.yaml
cross_engine:
  leadgen_db: "../LeadGen/data/leadgen.db"   # null to disable
  schedbot_db: "../SchedBot/data/schedbot.db" # null to disable
```

```python
from propgen.cross_engine import fetch_lead, fetch_appointment

snap = await fetch_lead(config, "lead-abc123")           # → LeadSnapshot | None
snap = await fetch_appointment(config, "appt-def456")    # → AppointmentSnapshot | None
```

Both functions:
- Return `None` if the configured path is unset, the file is missing,
  the row doesn't exist, or the schema has drifted in a way that breaks
  the SELECT.
- Open the SQLite file in `mode=ro&immutable=1` mode — PropGen cannot
  accidentally write, and SQLite skips the write-lock check (so an
  active sibling process holding a write lock doesn't block the read).
- Log at INFO on any tolerated failure (missing file, missing column).
  PropGen falls back to operator-supplied scope.

---

## LeadGen schema contract

PropGen reads from the `leads` table:

| Column                    | Type    | Required? | What PropGen does with it                                              |
|---------------------------|---------|-----------|------------------------------------------------------------------------|
| `id`                      | TEXT    | yes       | Lookup key passed by the caller. Stored on `Proposal.lead_id`.         |
| `contact_name`            | TEXT    | no        | Becomes `Proposal.client.name`.                                        |
| `contact_email`           | TEXT    | no        | Lower-cased, becomes `Proposal.client.email`.                          |
| `company_name`            | TEXT    | no        | Becomes `Proposal.client.company`. Also seeds the proposal subject.    |
| `status`                  | TEXT    | no        | Surfaced in `LeadSnapshot.status` for the AI seam to reference.        |
| `score`                   | REAL    | no        | Surfaced in `LeadSnapshot.score`. Not used directly today.             |
| `intake_notes`            | TEXT    | no        | Becomes `Proposal.intake_notes`. **Falls back to `notes`** if absent.  |
| `notes`                   | TEXT    | no        | Used as `intake_notes` fallback (older LeadGen schemas).               |
| `last_contacted_at`       | TEXT    | no        | ISO 8601 datetime. Surfaced for the AI seam to reference.              |

The SELECT is `SELECT id, COALESCE(...) FROM leads WHERE id = ?`, so
missing optional columns degrade to empty strings rather than blow up.

If `leads` doesn't exist (e.g. the operator's LeadGen DB was dropped
between deployments), the SELECT raises `OperationalError` and we
return `None` — PropGen continues without lead context.

---

## SchedBot schema contract

PropGen reads from the `appointments` table:

| Column           | Type    | Required? | What PropGen does with it                                          |
|------------------|---------|-----------|--------------------------------------------------------------------|
| `id`             | TEXT    | yes       | Lookup key passed by the caller. Stored on `Proposal.appointment_id`. |
| `client_name`    | TEXT    | no        | Becomes `Proposal.client.name`.                                    |
| `client_email`   | TEXT    | no        | Lower-cased, becomes `Proposal.client.email`.                      |
| `client_phone`   | TEXT    | no        | Becomes `Proposal.client.phone`.                                   |
| `service_slug`   | TEXT    | no        | Surfaced in `AppointmentSnapshot.service_slug`. Drives proposal subject. |
| `service_name`   | TEXT    | no        | Surfaced in `AppointmentSnapshot.service_name`. Preferred for subject. |
| `start_at`       | TEXT    | no        | ISO 8601 datetime. Surfaced for the AI seam to reference.          |
| `intake_notes`   | TEXT    | no        | Becomes `Proposal.intake_notes`.                                   |
| `notes`          | TEXT    | no        | Used as `intake_notes` fallback (legacy SchedBot schemas).         |
| `status`         | TEXT    | no        | Surfaced in `AppointmentSnapshot.status` for the AI seam.          |

The SELECT is `SELECT id, COALESCE(...) FROM appointments WHERE id = ?`,
so missing optional columns degrade to empty strings.

If `appointments` doesn't exist, the SELECT raises `OperationalError`
and we return `None` — PropGen continues without appointment context.

---

## Schema-drift tolerance policy

The contract is intentionally narrow — only the columns above are
required. Three failure modes are tolerated, in order of likelihood:

1. **Missing file** — log INFO, return None. The operator probably
   hasn't enabled cross-engine in their config (or the path is wrong).
2. **Missing row** — return None silently. The caller passed an id
   that isn't in the sibling DB. Not a PropGen problem.
3. **Missing table or column** — log INFO with the SQLite error, return
   None. The sibling engine has shipped a schema migration the
   contract doesn't yet cover. PropGen continues; the operator can
   either pin the sibling engine version or open a PR to expand the
   contract.

We deliberately do NOT fail loudly on schema drift because:
- PropGen's primary intake paths (web form, email parser, MCP tool
  call) don't depend on cross-engine reads — they're a convenience.
- A loud failure would block proposal creation just because a sibling
  engine moved a column.
- The fallback (operator supplies scope explicitly) is always available.

---

## Adding new columns to read

When PropGen wants to read a new sibling column, the procedure is:

1. Add the column to the relevant `Snapshot` dataclass in
   `propgen/cross_engine.py`.
2. Add the column to the `COALESCE(...)` clause in the SELECT, with a
   sensible default so missing-column drift is non-breaking.
3. Update this doc.
4. Coordinate with the sibling engine's CLAUDE.md — promote the column
   to "documented for cross-engine use" so future migrations consider
   PropGen.

Never add a JOIN, GROUP BY, or transaction-spanning query. Cross-engine
SELECTs are bounded — one row by id, one round-trip — so a partially
locked sibling DB never blocks PropGen for long.

---

## Why not just import the sibling package?

Three reasons:

1. **Independent deployability.** A PropGen deployment may not have the
   sibling engine installed at all. A persona repo (`agents/sage/`)
   pins PropGen as a dep but not LeadGen / SchedBot.
2. **Independent upgrade cycles.** A sibling-engine major-version bump
   would force PropGen to bump too, even if the wire-format the SQLite
   exposes hasn't changed.
3. **No transitive dep storm.** LeadGen brings its own AI / HTTP / DB
   deps. Importing it would balloon PropGen's install footprint by
   2–3× for a feature most operators don't use.

The SQLite contract is small enough that the friction of maintaining
it is much less than the friction of taking the package dep.
