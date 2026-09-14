# project.md — source of truth

Take-home for **interface.ai**. This file is the plan. We tick things off here.

Rule for this doc: plain language, short sentences. If something here reads like
a thesis, rewrite it.

---

## 0. Where things stand (2026-09-12, end of day)

**All six core requirements are built, demonstrated and independently verified.** The phase
plan below is history. Read this section first.

Three capabilities, all recorded by real LLM runs: `open_new_account_funded_from_account`,
`apply_for_loan_with_down_payment` and `pay_bill_phone_account_from_account`. 168 tests. Pushed to github.com/PradHolla/FinAutomate,
repo is public.

### What the system does, in one line

The model discovers a UI flow once. The artifact is the reusable capability. Replay is how it
runs in production, with no model in the loop.

### Everything added after Phase 5

**The model designs its own contract.** Only credentials go on the command line. It reads the
values out of the goal and declares its own typed inputs at `done`. It is held to that: a
parameter it names but no step sets fails the run. One pause fires before the first
irreversible step. 88 lines of gate machinery were deleted to get here.

**Re-recording.** `discover --rerecord <artifact>` hands the model the contract a capability
already has, so its callers survive a re-record, then rediscovers every locator. `version`
moves only if inputs or outputs actually changed. An existing capability is never overwritten
without the flag; the new recording lands beside it as `.new.yaml`. Provenance chains through
`recorded.supersedes`.

**Contracts are readable.** `replay --dry-run` prints `takes: username, password*, ...` and
works with no arguments, because the model picks the parameter names and does not pick the
same ones twice.

**Text is readable.** A visible element with a stable id and no controls inside it is now a
control with role `text`. That means a declared outcome can quote the application: `LOAN_DENIED`
reports the bank's own refusal wording instead of a fixed string.

**Two recovery verbs**, `restart` and `dismiss`. **All six of the brief's runtime conditions are
handled.** The last one, a validation error, needed a third capability against Bill Pay, because
the other two flows collapse a bad value into a generic error page.

**The agent loop has tests.** 13 of them, driving a scripted screen and scripted model turns.
Writing them found discovery was typed against the browser driver while only ever using the six
`Surface` methods, so it now takes the protocol. That fake screen is a second implementation of
the seam, which is the evidence behind the desktop-driver claim.

**Security.** Page content is fenced as untrusted. Config denies paths as well as control names,
after a discovery run walked around the Log Out block by navigating to the logout URL. Four
adversarial discovery runs are in `evidence/`.

### Documents

| | |
|---|---|
| `README.md` | setup, every scenario, every flag, and how to read the output |
| `REPORT.md` | the seven headings the brief asks for, word for word. 1,616 words, about 3 pages |
| `FINDINGS.md` | every bug this project found in itself, ordered to match the report |
| `evidence/README.md` | what each of the 27 runs shows |
| `PROBLEMS.md` | gaps found re-reading the brief. A working list, kept out of the repo |

`REPORT.md` is deliberately short and points into `FINDINGS.md` for detail, so a reviewer can
stop after the report and still have the argument.

### The evidence set

Rebuilt from scratch on 2026-09-12 against the current code, so nothing predates a change.
9 discovery runs (3 current, 2 superseded, 4 adversarial) and 15 replays. The handover run was
driven by hand in a live browser.

**Standing rule:** after any verification pass, prune `evidence/` back to what
`evidence/README.md` names. Check both directions - every run named exists, and every directory
is named.

### Human in the loop during discovery (2026-09-14)

The brief's section 3.6 names three escalation triggers. A re-read found we handled one. All
three are now done:

- **A risky step in replay** with nobody watching. Always worked.
- **The model stuck during discovery.** `discover --wait-for-human N` holds the live session,
  raises a request, and records what the person does *as steps in the recording* - their click
  matched back to a control on the held screen, built into a verified locator ladder.
  `resolve --handled --risky` marks that step as one a person must be present for, and every
  unattended replay honours it. If an action cannot be matched to exactly one control, no
  capability is written at all.
- **A replay hard failure.** Done on 2026-09-14. `_stuck` raises a request before giving up, and
  a person who can see the control clicks it. Evidence: `evidence/replay-120b1dc418/`.

Evidence: `discovery-8fd30e32f8` plus the two replays beside it. Built with `pnh` driving the
browser; six bugs came out of it, all in `FINDINGS.md` under *Bringing a person into a
recording*. Every one needed a real person and a real browser to find.

Also added: policy rules can name a role, `button:Open New Account`, because the target app
labels the menu link and the submit button identically and one rule denied both.

### What is left

**`PROBLEMS.md` was the live list, and every item on it is now closed.** It is a working file, not
in the repo; what it held is summarized below.

The last one, on 2026-09-14: a replay hard failure used to report and exit 1 without asking anybody,
which was the third of the brief's three escalation triggers. `_stuck` now sits beside `_fail` and
raises a request first. Two failures still never reach a person - one the artifact declared, and any
failure when `--wait-for-human` was not passed - so every existing run behaves exactly as it did.

The demo took some finding. Renaming a control does not break a ladder, because `anchored_role`
matches on role and position rather than on the name, so you have to hit a rung that was shallow to
begin with. Every `type` and `select` step has a `field_id` rung and ids belong to the vendor, which
leaves only the two clicks. `config/faults/tenant-b-redesign.yaml` renames the last six rungs of the
login button's ten and all ten miss. Evidence: `evidence/replay-120b1dc418/`, driven by hand.

Earlier the same day, four documentation items closed in one pass on `REPORT.md`: the brief's exact
headings for 3, 4 and 5; a defense of Playwright and of writing our own extractor; a "what I would
build next"; and the containment cut naming the brief's own opening example. Two stale numbers went
with them - the report claimed 3,800 lines and 147 tests against 4,190 and 163.

**Not closed, and deliberately so.** A discovery handover writes `kind: "risky_step"`, which is not
what happened. A third value would be honest and would also make the shipped discovery evidence
stale unless that run is redone by hand. Named in `PROBLEMS.md` rather than left to be found.

Beyond that: containment (the largest real gap, documented) and a desktop driver (argued in
section 4, not built).

### Standing rules for this work

Run `uv run finautomate reset` before anything that creates data, and after. Never commit
`.env`. Use a verification subagent and make it run `discover` for real: four passes in a row
skipped it, which is how the agent loop went unverified for so long.

**`project.md` and `CLAUDE.md` are in the repo.** They were written as working documents and are
published unedited, because how the work was done is part of what is being shown.

---

## 1. What we are building, in plain words

Banks run old internal apps with no API. The only way to use them is to click
around like a human does.

So we build a system that:

1. Takes a goal in English, like *"log in and read the balance of account 12345"*.
2. Lets an LLM drive the real UI until the goal is done. This is slow and costs money.
3. Saves what worked as a **typed file** — a "capability". Inputs, steps, outputs, success check.
4. **Replays that file with no LLM.** Fast, cheap, same result every time.
5. Asks a human for help when it gets stuck, and lets that human drive the *same* browser.
6. Never does anything outside an allowlist, and never writes passwords or PII to disk.

One line to remember:

> **The model discovers once. The artifact is the reusable capability. Replay is how it runs in production.**

That is the whole product. Everything else is support.

---

## 2. How we get scored

Straight from Section 7 of the brief, in their stated priority order:

| Rank | Criterion | What it means for us |
|---|---|---|
| 1 | **System design** | Clean boundaries. The artifact schema and replay contract are *central*. |
| 2 | **Correctness of core loop** | Agent really completes a goal. Artifact really replays. |
| 3 | **Robustness / error handling** | Business outcome vs recoverable vs hard failure, kept separate. |
| 4 | **Human-in-the-loop** | Real mechanism. *"not just a TODO"* — their words. |
| 5 | **Generalization** | Credible story for legacy/desktop surfaces and many tenants. |
| 6 | **Safety** | Allowlist, risky actions, redaction. |
| 7 | **Code quality** | Readable, typed, tested where it counts, easy to run. |
| 8 | **Communication** | REPORT.md makes the reasoning obvious. |

And what they say they will **not** reward:

> *"We do not reward feature breadth, framework name-dropping, or building scaling
> infrastructure (queues, clusters, multi-tenant plumbing)."*

So: **no AWS, no queues, no database, no Docker Compose sprawl.** Single Python
process, files on disk. This is the opposite of my usual instinct and I have to
keep checking myself on it.

The bar they set (Section 5): **a thin-but-real version of all six requirements
beats a polished version of three.**

---

## 3. Decisions already made (and why)

Everything below was checked, not guessed.

### Target app: ParaBank, self-hosted

`docker run -d -p 8080:8080 parasoft/parabank` → `http://localhost:8080/parabank/`

- Real JSP/servlet banking app. Mixed table and div layout, zero test IDs
  (verified: 0 `data-*` attributes and 0/36 labelled inputs across 7 pages). We didn't write it,
  so we can't have rigged it in our favor. That is the main reason to use it
  over a hand-built fake app.
- Confirmed it publishes a **native arm64 image**, so it runs on the Mac without
  emulation.
- It's a bank. Our goals read like the brief's own examples.

**Note for README:** ParaBank also exposes SOAP/WSDL services. We ignore them on
purpose — the brief says API integration is the preferred path and is *out of scope*.
Saying this out loud shows we read Section 1.

### What we found in its markup (verified by fetching real pages)

This is the good stuff. It shapes the whole design.

| Finding | Why it matters |
|---|---|
| Link `href`s carry `;jsessionid=...` on a first page load (Tomcat rewrites until it confirms cookies work). The browser URL bar stays clean. | A scraped `href` is session-bound and dead next run. Narrower than it first looked — the page URL itself is stable. Record "click the link named X", not a scraped href. |
| Submitting the account form does **not** navigate. Same URL before and after. | The stronger reason URLs are useless here. A checkpoint must be "this element became visible", not "we reached a new page". |
| Login inputs: `<p><b>Username</b></p>` then a bare `<input name="username">`. No label, no aria-label, no placeholder. | Accessible name is **empty**. `get_by_role("textbox", name="Username")` **fails here**. Our fallback ladder is not theoretical — it is required on step one. |
| Submit is `<input type="submit" value="Log In">` | Accessible name **works** here. So some controls resolve at tier 1 and some don't. Honest, mixed picture. |
| Register page is `<td><b>First Name:</b></td><td><input id="customer.firstName">` | Table-cell proximity is the strategy that works. Also has a semantic id — a decent lower-tier fallback. |

### Perception: our own accessibility extractor, not `aria_snapshot()`

Checked this. Playwright's `page.aria_snapshot()` returns YAML for *comparing*
pages. It has **no element references**, so there is no way to go from a snapshot
node back to something clickable. It is an assertion tool.

So we split it:

- **Perceiving + acting** → our own small JS function injected into the page.
  It walks the DOM, computes role + accessible name + value for each control,
  and returns a flat list with a temporary `ref` per snapshot.
- **Checkpoints** → `expect(page).to_match_aria_snapshot(...)`. This is exactly
  what it's built for. Using the framework's own idiom here is a small, cheap
  quality signal.

Why our own extractor is the right call and not over-engineering: it is the seam.
A Windows UIA driver or a macOS AX driver would produce **the same flat control
list** from a completely different source. That makes the "how does this reach
desktop apps" answer real instead of hand-waving.

### LLM: Claude Sonnet 5, direct Anthropic API

```python
from anthropic import Anthropic

client = Anthropic()                    # reads ANTHROPIC_API_KEY
client.messages.create(
    model="claude-sonnet-5",
    max_tokens=8192,
    thinking={"type": "adaptive"},
    output_config={"effort": "high"},
    tools=[...],
    messages=[...],
)
```

- One environment variable: `ANTHROPIC_API_KEY`. No region, no IAM policy, no
  inference profile, no marketplace subscription.
- $2 / $10 per million tokens in/out. A discovery run costs cents.
- Prompt caching on the system prompt and tool definitions - cheap, and the
  discovery loop resends both on every turn.

**Why not Bedrock, having researched it properly.** The deciding question is who
has to run this. The reviewers will run the discovery command, and the difference
is one env var against an AWS account plus model access plus an IAM policy with a
non-obvious two-ARN requirement.

The credential-sharing side settles it. **Anthropic workspaces enforce a hard spend
limit** - hit it and requests return 429 instead of continuing to bill. AWS Budgets
cannot do this; it alerts, on a roughly daily refresh, and stops nothing. Handing
someone a capped API key is a different act from handing them AWS keys.

Bedrock is also 50 percent more expensive ($3/$15), and Sonnet 5 there cannot be
called by its plain model id at all - it is `INFERENCE_PROFILE` only, so every call
must go through `us.anthropic.claude-sonnet-5`.

**What we lose, and where it goes instead.** The genuine argument for Bedrock is
that a bank wants inference inside its own AWS account and compliance boundary.
That does not disappear - it becomes a paragraph in REPORT.md section 6 explaining
that the client constructor is the only line that changes. Section 3.7 asks us to
*design* for the real environment, not build it.

**Do not use the hosted computer-use tool.** Screenshot plus pixel coordinates
would poison the artifact - coordinates are the least stable thing you can record.
We hand the model a structured control list instead.

### Stack

**Python 3.14** · uv · Playwright · Pydantic v2 · Typer · pytest · `anthropic`

Checked the version question rather than defaulting:

| Package | Latest | Supports 3.14? |
|---|---|---|
| CPython | 3.14.7 stable (3.15 still at rc) | — 3.14 *is* current |
| Playwright | 1.62.0 | yes |
| Pydantic | 2.13.5 | yes |
| anthropic SDK | 1.4.0 | yes |

So **3.14**, not 3.12. Nothing blocks it. (Note the SDK is 1.x now — write 1.x
code, ignore any 0.x patterns from memory.)

Evidence and artifacts are plain files on disk. No server, no DB.

---

## 4. Cheap things that make this look senior

Small effort, real signal. Do all of these.

- [ ] **Put the "confirmation is a div swap, not a page load" finding in REPORT.md.**
      One sentence proving we looked at the real app instead of theorising. (The
      jsessionid angle is real but narrower — mention it only as a footnote about
      scraped hrefs, do not overstate it.)
- [ ] **Print discovery vs replay side by side.** `discovery: 47s, 18 model calls,
      31.2k in / 4.1k out tokens` / `replay: 3.1s, 0 model calls, 0 tokens`.
      One number that proves the whole thesis. Nobody else will have it.
      Report **tokens** alongside the dollar figure. Token counts are the fact;
      the price is a rate that can move.
- [ ] **Log which locator strategy resolved each step.** If step 3 needed tier-3,
      that step is fragile. Free brittleness report.
- [ ] **Say we skipped ParaBank's SOAP API on purpose**, because the brief says APIs
      are out of scope. Shows careful reading.
- [ ] **`replay --dry-run`** that prints the plan and touches no browser. Two lines
      of code, reads as production thinking.
- [ ] **Show a redacted log line in evidence** — `"password": "«redacted»"` — rather
      than only claiming we redact.
- [ ] **Real exit codes.** `0` success, `2` known business outcome, `1` hard failure.
      A caller can script against it.
- [ ] **One `make demo`** that runs the whole thread end to end.
- [ ] **Artifact readable in 30 seconds.** A human reviewer should open the JSON and
      instantly get what it does.
- [ ] **Screenshot on failure named after the step that failed**, not `error.png`.
- [ ] **A `CUTS` section in REPORT.md with real cuts**, stated plainly. No apologising.

---

## 5. Build phases

Estimated ~6 focused days total. Each phase ends with something that runs.

---

### Phase 0 — Ground truth · ~half day

**Goal:** stop guessing about ParaBank. Get facts.

- [x] Start Docker Desktop, run ParaBank, confirm it serves on `:8080` (~15s to boot)
- [x] Working login found: `john` / `demo`, pre-seeded. No registration needed.
- [x] Reset path found: `admin.htm` has INIT and CLEAN buttons
- [x] Walk the target flow by hand and write down every screen
- [x] Throwaway probe script dumping role + accessible name + id per control,
      read from the browser's own accessibility tree via CDP
- [x] Results recorded in `notes/surface-findings.md`

**Why it matters:** the locator ladder is the most graded piece of the project.
It has to be designed from real data. These notes become REPORT.md section 3.

**Done when:** we can say exactly which locator strategy each of ~15 real controls needs.

---

### Phase 1 — Artifact schema · ~half day of thinking, less of typing

**Goal:** design the thing they said is the focal point of the evaluation.

This is a design phase. Write it on paper first, argue about it, then make it Pydantic.

Must express (Section 3.2):

- [x] ordered steps / actions
- [x] how each control is identified — **the locator bundle**
- [x] typed input parameters
- [x] typed outputs and their shape
- [x] a checkpoint / success condition
- [x] version + metadata so it's reviewable

Rough shape to argue about in Phase 1:

```
Capability
├─ name, version, description        ← agent-readable contract
├─ target: app id + entry point       ← never a jsessionid URL
├─ inputs:  [{name, type, required, sensitive}]
├─ outputs: [{name, type, source}]
├─ steps:   [Step]
│    ├─ id, action (click/type/select/navigate/read/assert)
│    ├─ target: LocatorBundle
│    ├─ value: literal or {{param}}
│    ├─ risk: safe | risky
│    └─ checkpoint: optional per-step assertion
└─ outcomes: [{name, detector, classification}]   ← "no such account" lives here
```

The **LocatorBundle** is the heart:

```
LocatorBundle
└─ strategies: ordered list, first match wins
   1. role + accessible name       ← works on "Log In"
   2. label / anchor proximity     ← required for ParaBank's username field
   3. stable id or form field name  ← customer.firstName
   4. text content
   5. bbox coordinates              ← last resort, flagged as fragile
```

Key design points to be ready to defend:

- Bundle is **ordered and declarative**, not a single selector. Replay records
  which tier hit — that is our drift signal *and* our multi-tenant story.
- Coordinates exist but are last and flagged. Honest, not absent.
- `sensitive: true` inputs are never written to the artifact or the logs.
- Business outcomes are **declared in the artifact**, not discovered at runtime.
  "No such account" is part of the capability's contract.

**Done when:** the schema is Pydantic, a hand-written example artifact validates
against it, and a person can read that example and understand the flow.

---

### Phase 2 — Surface driver · ~1 day

**Goal:** the seam between "how we see and touch a screen" and "the recorded flow".

- [x] `Surface` protocol: `observe() -> Snapshot`, `act(Action) -> ActionResult`
- [x] `Snapshot` = flat list of `Control{ref, role, name, value, enabled, bbox, frame}`
- [x] `BrowserSurface` implementation over Playwright
- [x] JS extractor that computes role + accessible name per element
- [x] Locator bundle **resolution**: try each strategy in order, return the first
      that matches exactly one element, report which tier won
- [ ] Locator bundle **construction**: given a control the model picked, build the
      ordered bundle for the artifact
- [x] Waiting strategy — poll `observe()` until the declared checkpoint holds.
      NOT network idle: measured against the real app it returns before the page
      updates. Never `sleep()`.

**Done when:** a plain Python script (no LLM) can log into ParaBank using only
role/name/anchor targeting, and tell us which tier resolved each control.

**Why it matters:** this is the answer to REPORT.md section 4. If the driver is
clean, "swap it for Windows UIA" is obviously true. If it leaks CSS selectors
upward, that section is a lie.

---

### Phase 3 — Discovery loop · ~1 day

**Goal:** the real LLM run. The brief says this is the one thing we cannot fake.

- [x] Tool definitions for Claude (`claude-sonnet-5`): `observe`, `click`, `type`,
      `select`, `navigate`, `read_value`, `done`, `stuck`
- [x] Loop: observe → send snapshot → model picks a tool → policy check → act → repeat
- [x] Stopping conditions: max steps, wall-clock timeout, dead-end, model says `stuck`
- [x] **Policy gate on every single action**, before it runs
- [x] Record every accepted action into a trace
- [x] Turn the trace into a `Capability` artifact — build locator bundles here
- [x] Structured JSONL log of what the model did and why

**Done when:** `python -m app discover --goal "..." --target parabank` produces a
saved artifact and a full evidence trail.

**Notes:**
- Keep the raw model transcript **out** of the artifact. The brief explicitly says
  the artifact must be decoupled from the transcript. Transcript goes to evidence.
- Turn on prompt caching for the system prompt and tool definitions. Cheap, and
  shows we know the API.

---

### Phase 4 — Replay engine · ~1 day

**Goal:** the production path. No LLM anywhere in it.

- [x] Load artifact, validate supplied params against declared input types
- [x] Execute each step: resolve bundle → act → verify step checkpoint
- [x] Verify the final success condition
- [x] Extract declared outputs and return them typed
- [x] **The error taxonomy — this is the graded part:** all three classes now
      trigger on demand through the Phase 5 proxy, not just on paper

| Class | Example | What we do |
|---|---|---|
| **Business outcome** | "no such account", requested option absent from a list | Return it as a *result*. Not an error. Exit 2. |
| **Recoverable** | slow load, known interstitial, session expired | Retry / dismiss / re-auth, bounded. Log it. Continue. |
| **Hard failure** | control not found, checkpoint failed | Stop. Screenshot. Report step + expected + observed. Exit 1. |

- [x] `ReplayResult` as a discriminated union — the *types* enforce the distinction
- [x] `--dry-run`
- [x] Screenshot + DOM snapshot on hard failure

**Done when:** `python -m app replay <artifact> --params '{"account_id":"12345"}'`
returns structured output, and a bad account id returns a *business outcome*
rather than a stack trace.

**The trap to avoid:** conflating "no such account" with a crash. The brief calls
this *"the most common design mistake here"*. Our type system should make it
impossible.

---

### Phase 5 — Fault injection proxy · ~half day

**Goal:** one small component that pays for three deliverables.

A ~100 line reverse proxy sitting between our driver and ParaBank. Driver points
at `:8888`, proxy forwards to `:8080`, and a YAML rule file can:

- [x] delay a matching request → **transient slowness**
- [x] return 500 → **app error**
- [x] drop the `JSESSIONID` cookie → **session expiry, instantly, on demand**
- [x] rewrite text in the HTML → **tenant B**, and any reworded page
- [ ] ~~inject a modal div → unexpected dialog~~ — cut. `replace` can do it, but
      nothing in the capability declares an interstitial to dismiss, so it would
      have been a fault with no matching handler. Not shipped rather than shipped
      and unused.

**Built:** `src/finautomate/faultproxy.py`, `finautomate proxy`, and four rule files
in `config/faults/`. Four faults, not six: `replace` covers the permission-denied
page and the modal, so they are the same primitive with different YAML.

**What it caught.** Two real bugs, which is the justification for building it:

1. Recovery was consulted only when a *control* could not be found. In this flow
   every navigation is followed by a checkpoint, so an expired session always
   arrived as "the confirmation never appeared" and was reported as a hard failure.
   The declared recovery had never once run.
2. An outcome classified `hard_failure` could declare a detector, and nothing read
   it. The report said the expected thing was missing, which points the reader at us
   instead of at the application.

Both fixed, both now covered by tests against a handmade snapshot rather than a
browser. The schema now also rejects a `recoverable` outcome that declares no
recovery or no detector - it read fine and could never have worked.

**Known limit found, then fixed.** Checkpoints were recorded as one literal string
with no fallback, so a reworded page turned a healthy run into a hard failure even
though every locator survived. The recorder now writes a checkpoint on the *control*
that appeared - addressed through the same ladder as any locator - and falls back to
text only when an action produces no new control. Measured on the three real
transitions: two produce a control carrying a vendor element id, which no amount of
rebranding moves; the third produces only navigation links named in the
institution's own words, and there is nothing better available there.

Result: the tenant-B rules file went from three frozen strings to two, and the
replay from 7 of 8 steps falling back to 8 of 8. Both remaining frozen strings are
now *locator* ladder depth, not checkpoint brittleness.

Found on the way: `record.py` proposed `role_name` from a control's accessible name
with no digit guard, though the same guard existed two lines below for `text`.
Harmless while bundles were only built for controls an action was about to touch;
not harmless for a checkpoint on the confirmation panel, whose link is named after
the account number. Guarded, with a regression test.

**Looked up and not used:** Playwright's `expect(page).to_match_aria_snapshot()`.
Partial matching, regex names and role-without-name all point the same way we went,
which was worth confirming. Not adopted: it is an `expect` assertion built for a
test runner rather than a poll for a production engine, and putting it in the
artifact schema would tie the recording to a browser. Checkpoints poll
`surface.observe()` so that a desktop driver inherits waiting for free, and that
seam is the design's central claim.
