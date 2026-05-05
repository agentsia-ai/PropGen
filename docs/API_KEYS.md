# API Keys & Credentials Setup

PropGen is e-sign-provider-agnostic, but each integration has a
slightly different credentials shape. This doc walks through every
supported provider; you only need to set up the one(s) you actually
plan to use.

The strong recommendation is **start with DocuSign** — it's the
ubiquitous SMB and enterprise default, and the JWT-Grant flow means no
per-user OAuth dance.

---

## Anthropic (required for everything)

PropGen uses Claude for request classification, proposal drafting, and
the pricing assistant.

1. Go to https://console.anthropic.com → sign up
2. **API Keys → Create Key**
3. Copy the key into `.env`:
   ```
   ANTHROPIC_API_KEY=sk-ant-...
   ```

Free tier includes $5 of credits — plenty to test the engine end to end.

---

## DocuSign (recommended primary)

Why this one first: ubiquitous in SMB and large enterprise alike, JWT
Grant means tokens auto-refresh without operator interaction, and the
free developer account is fully featured.

### Step 1 — Create the developer account

1. Go to https://developers.docusign.com → **Create Free Developer Account**
2. After signup you'll be redirected to https://account-d.docusign.com/
   — bookmark this. It's the **demo** environment (everything you wire
   up here is for testing; real signatures use `account.docusign.com`).

### Step 2 — Create the integration app

1. **Settings → Apps and Keys → Add App and Integration Key**
2. Give it any name (e.g. "PropGen — &lt;your business&gt;").
3. Under **Authentication**, enable BOTH:
   - **Authorization Code Grant** (needed for the one-time consent step)
   - **JWT Grant** (the runtime flow PropGen uses)
4. Copy the **Integration Key** (a UUID) into `.env`:
   ```
   DOCUSIGN_INTEGRATION_KEY=00000000-0000-0000-0000-000000000000
   ```

### Step 3 — Generate the RSA keypair

In the same App config screen:

1. Scroll to **Service Integration → RSA Keypairs → Add RSA Keypair**.
2. Save the **private key** PEM somewhere safe on your local filesystem
   (do NOT check it into git). Default expected location:
   `./docusign_private.key` (`.gitignored` already).
3. Add to `.env`:
   ```
   DOCUSIGN_PRIVATE_KEY_PATH=./docusign_private.key
   ```
   *(Alternatively, paste the full PEM into `DOCUSIGN_PRIVATE_KEY`
   directly — useful for serverless deployments where reading from disk
   is awkward.)*

### Step 4 — Find your User ID and Account ID

1. **Settings → Apps and Keys** — the **API Username** at the top of the
   page is your User ID (a UUID).
2. **Settings → Plan and Billing** — the **API Account ID** is shown
   there (also a UUID).
3. Add to `.env`:
   ```
   DOCUSIGN_USER_ID=00000000-0000-0000-0000-000000000000
   DOCUSIGN_ACCOUNT_ID=00000000-0000-0000-0000-000000000000
   ```

### Step 5 — One-time consent

DocuSign requires the API user to grant the integration consent to
impersonate them via JWT. This is a one-time browser step.

The first time PropGen tries to fetch a token, it'll fail with
`consent_required` and print a URL like:

```
https://account-d.docusign.com/oauth/auth
  ?response_type=code
  &scope=signature+impersonation
  &client_id=<your-integration-key>
  &redirect_uri=https://www.docusign.com
```

Open it in a browser, sign in as the API user, and click **Allow Access**.
Done — every subsequent token fetch is non-interactive.

### Step 6 — Test it

```bash
uv run propgen docusign ping
```

You should see:

```
OK DocuSign auth round-trip succeeded.
  user: you@example.com  (00000000-...)  name: Your Name
  account: 00000000-...  ★  name=Your Account  base_uri=https://demo.docusign.net
```

If you see `consent_required`, walk through Step 5.

### Step 7 — Webhook secret (Connect)

For real-time envelope status (sent / viewed / signed / declined / voided)
back into PropGen:

1. **Settings → Connect → Add Configuration → Custom**
2. **URL to publish to**: your public webhook endpoint (a small
   FastAPI / Lambda / Cloudflare Worker that calls into
   `propgen.sources.docusign_webhooks.DocuSignWebhookReceiver.handle()`).
3. **Data Format**: **JSON** (NOT XML — the receiver only decodes JSON).
4. **HMAC Signature**: ON. Generate a secret, copy it to `.env`:
   ```
   DOCUSIGN_WEBHOOK_SECRET=<the-secret-you-just-set>
   ```
5. **Trigger Events**: enable
   - Envelope Sent
   - Envelope Delivered (= recipient opened)
   - Envelope Completed (= all signers signed)
   - Envelope Declined
   - Envelope Voided
6. (Optional) **Include Documents** OFF and **Include Document Fields**
   OFF — PropGen only uses the envelope-level status.

The receiver verifies the HMAC-SHA256 signature on every incoming
request against this secret. **Leaving the secret blank disables
verification** — only safe behind an authenticated edge.

### Step 8 — Switch to production

When you're ready to send real proposals, update `config.yaml`:

```yaml
docusign:
  base_url: "https://www.docusign.net/restapi"
  oauth_host: "account.docusign.com"
```

…and repeat steps 2–5 inside the production DocuSign account (the
integration key is per-environment).

---

## Dropbox Sign (alternative, stub in v0.1.0)

The class shape is wired up but `send_envelope` raises `NotImplementedError`.
To enable, set `DROPBOX_SIGN_API_KEY` in `.env`, set
`dropbox_sign.enabled: true` in `config.yaml`, and implement the REST
calls in `propgen/sources/dropbox_sign.py`. PRs welcome.

```
DROPBOX_SIGN_API_KEY=...
```

API reference: https://developers.hellosign.com/api/reference/

---

## PandaDoc (alternative, stub in v0.1.0)

Same shape — class exists, send raises. Set `PANDADOC_API_KEY`,
`pandadoc.enabled: true`, and implement
`propgen/sources/pandadoc.py`.

```
PANDADOC_API_KEY=...
```

API reference: https://developers.pandadoc.com/reference/

---

## SMTP (sending the cover note + follow-ups)

The engine drafts the cover note that accompanies the DocuSign envelope
plus follow-up nudges. Wire up SMTP for delivery (downstream personas
own the actual sender — the engine produces the drafts and stops).

The `.env` shape covers Gmail SMTP:

```
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=you@gmail.com
SMTP_PASSWORD=<app-password>          # https://support.google.com/accounts/answer/185833
SMTP_FROM_EMAIL=you@gmail.com
SMTP_FROM_NAME="Your Business Name"
```

For Gmail accounts, **use an app password**, not your real password.
2-step verification must be on first.

---

## Twilio (optional SMS follow-ups)

Install the optional dep:

```bash
uv sync --extra sms
```

Then:

1. Sign up at https://www.twilio.com
2. **Console → Account → API Keys & Tokens** — copy the Account SID +
   Auth Token.
3. Buy or port a phone number under **Phone Numbers → Manage → Active**.
4. Add to `.env`:
   ```
   TWILIO_ACCOUNT_SID=AC...
   TWILIO_AUTH_TOKEN=...
   TWILIO_FROM_NUMBER=+15555550100
   ```

In `config.yaml`, follow-ups will fall back to SMS automatically when a
proposal's client has no email — no further toggle needed.

---

## Inbound webhook receiver

If you accept proposal requests from a website contact form or an
embedded widget, sign every request HMAC-SHA256 with a shared secret:

```
WEBHOOK_SIGNING_SECRET=<long random string>
```

Then on the form-handler side, sign the JSON body with the same secret
and pass the signature in the header configured under
`webhook.hmac_header` in `config.yaml` (`X-PropGen-Signature` by
default). Leaving the secret blank disables verification — only safe
if the receiver is already behind an authenticated edge.

---

## Where credentials live

| Item                              | Lives in                                    | Gitignored? |
|-----------------------------------|---------------------------------------------|-------------|
| API keys, tokens, secrets         | `.env`                                      | yes         |
| DocuSign RSA private key (PEM)    | `./docusign_private.key` (path configurable) | yes         |
| DocuSign cached access token      | `./.docusign_token.json` (path configurable) | yes         |
| Business config + identity        | `config.yaml`                               | yes         |
| Brand assets (logo, etc.)         | `./assets/` (path configurable)             | no (commit safe) |

Never commit any of `.env`, the private key, or the token cache. The
`.gitignore` already covers them.
