# SC-42 — Spike: bring-your-own AI key for alpha

**Status:** recommendation, 2026-10-05. Based on the SC-42 card; I have not seen the
"full brief in the Scout project docs," so anything it constrains that the card doesn't
say is not reflected here. Provider API behaviour below is from memory and flagged
**[verify]** where the design depends on it.

## Recommendation in one paragraph

Alpha testers paste an **Anthropic API key** into a settings page. Scout validates it with a
one-token test call, stores it **encrypted at rest, per user**, and uses it only for work a
tester triggers (coach Q&A, parsing, on-demand scoring) — never for shared work and never from the
GitHub Actions scheduler. Every AI step is an idempotent job row, so a dead key or empty balance
pauses work instead of losing it, and the tester gets one clear "reconnect" message. Build the
provider seam now (tiered per-skill model routing), but ship Anthropic only; add OpenAI only if a
tester can't get an Anthropic key. Roughly **7–9 working days** for the build, after a 1-day spike.

## 1. Which steps use the tester's AI

Rule: **BYO key only for work a specific user triggers and that benefits only that user.**
Anything shared across users must not run on one tester's key.

| Step | Use tester's key? | Why |
|---|---|---|
| Coach Q&A | **Yes** | Interactive, low volume, the feature testers came for. Standard-tier model. |
| Parsing (resume / profile) | **Yes**, once per user | Per-user, one-time, small. Fast-tier model. |
| Scoring (posting vs. user) | **Yes, but on demand only** | Highest volume (postings × users) and the real cost driver. Score when the user opens or asks for a posting, cache on `user_postings`. Fast-tier model, per-user daily cap. |
| Anything shared (posting normalisation, dedup) | **No** | Would bill one tester for everyone's benefit. In alpha: don't use AI for it. |

Consequence: **no background scoring in alpha.** That keeps decrypted keys out of the scheduler
and out of GitHub Actions secrets entirely.

## 2. Pasted key vs. MCP

**Pasted key.** MCP inverts control: the tester's own AI client calls Scout's tools. That needs an
MCP-capable client, can't run inside the dashboard, gives Scout no control over prompts or model,
and can't do any background work. It stores no key, which is attractive, but it's a different
product shape. Revisit later as an optional power-user path; not the alpha path.

## 3. Test call at connection

Yes. On submit, make one call with `max_tokens: 1` on the cheapest model (Haiku 4.5,
`claude-haiku-4-5-20251001`) — well under a cent. Classify the result and only store the key if it
is usable:

- 200 → store, `status = active`, record `validated_at`
- 401/403 → reject, "that key wasn't accepted"
- billing/credit error → store optionally as `no_credit`, tell them to add credit **[verify exact shape]**
- 429 → "rate limited, try again in a minute" (don't store yet)
- network / 5xx → retry once, then "couldn't reach Anthropic"

Show only `…last4` afterwards; never echo the key back.

## 4. Credit / rate-limit failures mid-run

Classify every provider error into four buckets, one function, unit-tested with a fake provider:

| Class | Typical signal | Behaviour |
|---|---|---|
| `auth` | 401/403 | Mark key `invalid`, stop retrying, show "reconnect your key" |
| `no_credit` | Anthropic: 400 with a credit-balance message; OpenAI: 429 `insufficient_quota` **[verify; message-matching is brittle]** | Mark `no_credit`, stop retrying, show "add credit" |
| `rate_limited` | 429 + `retry-after` | Exponential backoff, a few retries, then park the job |
| `transient` | 5xx / 529 overloaded / timeout | Backoff retries, then park |

**Work survives** because every AI step is a row in an `ai_jobs` table
(`user_id, kind, input_ref, status, attempts, error_class, result`). `auth`/`no_credit` set
`status = blocked_key`; reconnecting the key flips those back to `queued`. Partial results are kept.

**What the tester sees:** a status chip/banner in the dashboard plus **one** ntfy to their own topic
when the key first goes bad (not one per failed call). The operator health channel gets an
aggregate only (e.g. "2 users blocked on keys"), reusing SC-31's alert path.

**Protect the tester's bill:** a per-user daily call/token budget enforced before each call, so a
bug or a heavy day can't run up their account unexpectedly.

## 5. Anthropic, OpenAI, or both

**Anthropic only to start.** Supporting both doubles the error taxonomy, structured-output quirks and
prompt tuning for no alpha benefit. But build the seam now:

- `llm.complete(skill, messages)` — callers never name a provider or model.
- A routing table `skill → tier` (`fast` / `standard` / `deep`) and `(provider, tier) → model id`.
  Initially: parsing + scoring → `fast` (Haiku 4.5), coach → `standard` (Sonnet 5.5).
- Per-user `provider` column on the credentials row.

Cheapest way to decide on OpenAI: **ask Damian and Corey which key they have or can get.** Add the
adapter only if one of them can't use Anthropic (~2–3 days).

## 6. Storage, access, revocation

`user_ai_credentials(user_id PK → users, provider, key_ciphertext, key_last4, status, enc_version,
validated_at, created_at)`.

- **Encryption:** application-level AES-256-GCM (or Fernet) with the key in a Render env var
  (`AI_KEY_ENC_KEY`), `enc_version` for rotation. A Neon-only leak doesn't expose keys.
- **Access:** decrypt only inside the request/job making the call, in the web process. Never logged,
  never sent back to the browser, scrubbed from error reports. Every query scoped by the session's
  `user_id`; no admin screen that shows keys.
- **Revocation:** "Disconnect" hard-deletes the ciphertext immediately; removing a tester (with
  `revoke_user_sessions`) also deletes their credentials row. Tell users to rotate the key at the
  provider too.
- **Be honest in onboarding:** the operator holds the encryption key, so this isn't zero-trust. Ask
  testers to create a **dedicated key with a spend limit** in the provider console.

## Effort estimate (Anthropic-only)

| Piece | Days |
|---|---|
| Credentials table, encryption, settings page, test call | 1.5–2 |
| LLM client seam, tier routing, error classification | 1.5 |
| `ai_jobs`, retry/park/resume, budget guard | 2 |
| Dashboard status chip + ntfy + health aggregate | 1 |
| Onboarding explainer (why a key; how to get one; spend limit) — design + copy | 1 |
| Fake-provider tests (inject each error class; cross-tenant isolation) | 1 |
| **Total** | **~7–9** |

OpenAI adapter: +2–3. Spike itself: 1 day, up front.

## Highest-risk unknowns (in order)

1. **Will testers actually get an API key?** An API key needs a console account with billing and
   prepaid credit — a Claude.ai subscription doesn't include API credit. This is the biggest threat
   to alpha engagement and costs nothing to answer: ask Damian and Corey this week.
2. **Real cost per tester.** Unknown until scoring volume exists. Record token `usage` on every
   call from day one — it's the data the $9–30/mo cost sketch needs.
3. **Provider terms for holding users' keys server-side.** I believe it's allowed when the user
   supplies their own key, but I haven't verified either provider's current terms. Check before building.
4. **Credit-exhausted detection.** Depends on provider error shape/message **[verify against a live
   zero-credit key]**; string matching is brittle. Design the classifier so a miss degrades to
   `transient` → parked, not lost.
5. **Single encryption key in one env var.** Fine for 2–3 testers with spend-limited keys; not a
   launch posture.
6. **Model parity.** Fast-tier output quality for parsing/scoring is unproven; scoring logic is out
   of scope here.

## Spike format (multi-tenant)

Time-boxed to **one day**, on a branch, ending in a pass/fail checklist rather than a demo:

1. Two fake tenants (A, B), each with their own credentials row.
2. A **fake provider server** that returns scripted outcomes (200, 401, credit-exhausted, 429, 529).
3. Run the same jobs for A and B and assert:
   - A's key is never used for B's jobs, and B's data never reaches A's calls (isolation test).
   - Each error class lands in the right state, and `blocked_key` jobs resume after reconnect.
   - Disconnect deletes the ciphertext; a DB dump contains no plaintext key.
   - Nothing in logs/exceptions contains a key.
4. Output: the checklist, measured token counts per skill, and an updated effort number.

## Out of scope (per the card)

Pricing, launch API architecture, scoring logic.
