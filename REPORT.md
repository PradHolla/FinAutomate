# Design write-up

The target is **ParaBank**, a real JSP banking demo from Parasoft, run in Docker. We did not
write it, and that matters: its inputs have no labels and its forms never change URL on
submit. Both shaped the design.

This is the short version. `FINDINGS.md` has the bug behind each decision, `evidence/` a run
behind each claim.

---

## 1. Architecture

Two paths through one codebase. **Discovery** runs once: a model reads the screen, picks one
action, and we write down how to find that control again. **Replay** runs every time after,
with no model in it. Both drive the same `Surface` and resolve locators with the same code, so
a recording cannot mean one thing to the recorder and another to the replayer.

**The seam is a `Snapshot`**: a flat list of controls (role, accessible name, form attributes)
and text anchors in reading order. Above it, pure functions testable with no browser. Below it,
one driver per surface.

The agent loop is a while loop, not a framework: observe, ask for one action, check it, do it,
look again. Six decisions in it carry weight.

| Decision | Why |
|---|---|
| One action per turn | a second action chosen from the same snapshot is chosen blind |
| Tools, not prose | no gap between what the model said and what the harness understood |
| A stale reference is refused, not attempted | the harness enforces something the model cannot see, which is why these are specific tools and not one generic "do this" |
| The model never receives a secret | it names a parameter; this layer substitutes the value on the way to the browser |
| The model designs the contract | it declares its own inputs at `done`, and a parameter no step sets fails the run |
| One pause before the point of no return | a field left on its default is not in the recording, and after that click there is no way back |

It never sees a screenshot either, because a model that picks targets from pixels answers in
pixels. Guards that do not need it to choose to stop: 25 steps, 300 seconds, 200k tokens.

**The screen is untrusted.** Text in a back-office app was typed by a customer, so it is fenced
and the prompt says everything inside is data. That is the cheap half. The half that counts is
that the **allowlist does not care what the model decided**, proven by a test with the prompt
taken out entirely.

3,900 lines of Python, 140 tests, two capabilities, each recorded on two models. The last three
rows of that table exist because we shipped the bug first: *The agent loop* in `FINDINGS.md`.

---

## 2. Artifact schema

A capability is a function: typed inputs, ordered steps, typed outputs, a success condition,
and the business outcomes a caller needs. Pydantic v2, frozen, unknown keys rejected, written
to YAML so a person can read it.

Two rules are enforced by the types rather than documented. **No hostnames**: `entry` is a
relative path and a validator rejects any scheme, which is what lets one artifact serve many
institutions. **No CSS selectors and no coordinates**: controls are addressed the way a screen
reader addresses them.

**The locator bundle is the centerpiece.** Each step carries an ordered list of ways to find its
control, and replay takes the first matching *exactly one* element. Two matches is a failure, not
a coin flip. Across eight ParaBank screens there are **42 form fields and not one has an
accessible name**, so role plus name cannot find a single input here. Lower rungs anchor to the
nearest text in reading order; below those sit the form's own `name` and `id`.

The recorder checks every strategy it proposes with the real resolver, so recorder and replayer
cannot disagree, and refuses anything containing a digit. Both rules exist because we shipped the
bug first: *Recording* in `FINDINGS.md`.

---

## 3. Determinism and error handling

Replay is deterministic because it calls no model, resolves by the recorded ladder, and waits
on stated conditions rather than sleeps. Not on the network going idle either: measured here,
that fires *before* the panel swap lands.

| Result | Exit | |
|---|---|---|
| Success | 0 | with typed outputs |
| Business outcome | 2 | the application answered, and the answer was no |
| Needs human | 3 | stopped at a step a person has to decide |
| Hard failure | 1 | step, expected, observed, screenshot |

Exit 2 is the one that matters, and the loan capability produces both kinds: a funding account
the customer does not own (the caller got it wrong) and a refused application (the bank said
no). The types stop either being mistaken for a crash, because a business outcome has no
`screenshot` field and a failure has no `outcome` field.

**An outcome quotes the application rather than paraphrasing it.** ParaBank has four refusal
wordings depending on which of funds and down payment fell short, so `LOAN_DENIED` reads
whichever came back off the page.

**Two recovery verbs.** An expired session means every screen behind the login page is gone, so
`restart` returns to the entry point. A notice over an intact page means the flow underneath has
not moved, so `dismiss` clears it and retries the step. **Nothing is retried unless declared**,
with a detector and bounded attempts. Those conditions live in **tenant config, not the
artifact**: a recording holds only what the model saw, and nothing breaks on a healthy app.

Testing this needed a fault injector, since the app will not expire a session to order.
`finautomate proxy` misbehaves on request, and immediately found our declared recovery had never
once run. Everything it found: *Replay* in `FINDINGS.md`.

---

## 4. Other surfaces and other tenants

**Other surfaces.** The `Snapshot` vocabulary was chosen to exist off a browser: role,
accessible name, nearby text, reading order. Windows UI Automation and macOS Accessibility
expose the same, so a desktop driver fills in the same structure and nothing above the seam
changes, waiting included, because checkpoints poll `observe()`. That seam is not only
asserted: the agent loop's tests drive a second, non-browser implementation of `Surface`
through a whole discovery run, unchanged.

**Other tenants.** The artifact holds no hostname, so a second institution is a config change.
The fault proxy rewrites ParaBank into a differently branded bank, and the same artifact,
unedited, still runs:

```
click_log_in             click   [4] anchored_role  <- fallback
click_open_new_account_2 click   [5] anchored_role  <- fallback
SUCCESS    drift warning: 8 of 8 steps needed a fallback locator
```

Every step, including the irreversible one, found its control by a rung nobody would pick
first. Against the original tenant every step resolves at rung 0. That difference is the whole
argument for recording a ladder instead of a selector.

**Overrides**, for a tenant the ladder cannot absorb: a sparse patch keyed by step id, able to
replace a step's locator, checkpoint or risk, never to add, remove or reorder steps. That is a
different flow, and calling it an override grows patches into second recordings nobody can
review. Designed, not built.

**Drift** shows up as the rung that resolved, per step, per tenant, per run: rung 0 last month and
rung 2 today says the page moved and when. The answer is `discover --rerecord`, which hands the
model the contract it already has and rediscovers every locator. Against ParaBank it came back
identical. `version` moves only when inputs or outputs change, so a locator-only re-record leaves
every caller alone.

---

## 5. Escalation and handoff

A run is stuck when the model calls `stuck`, a guard trips, replay hits a hard failure, or an
irreversible step comes up with nobody watching.

**Who is in control is an explicit lease**: `agent`, `human` or `none`, in a file rather than in
memory, because the worker and the operator are different processes. The request carries which
capability, which step, why it stopped, a screenshot, and what was on screen.

**The person gets the same live session.** While they hold it, the page reports their clicks and
field changes into the same log as everything the machine did, because a bank cannot have a gap
reading "a human did something here". Password fields report `<redacted>`.

**Two decisions, kept apart.** `--approve` means the automation may act; `--handled` means the
person did it themselves and the step is skipped. Collapsing those loses the difference an audit
cares about. Control returns to the agent on every path, including rejection, because a lease
left with someone who walked away is how a run hangs forever.

`evidence/replay-275b89d0f4/` is a real one: held at the irreversible step, a person clicked the
button themselves, their click is in the record, control came back.

---

## 6. Safety

**An allowlist in config, checked before every action**, in discovery and in replay: a permitted
path prefix, permitted actions, controls the agent must never touch, and controls that create or
move something.

**Config decides what is risky, not the model.** An agent that reports "this one is fine" about
itself is the control that fails when it matters. **Risk belongs to the control, not the
action**: clicking is not dangerous, clicking the button that opens an account is.

**Secrets** never reach the model in discovery. At replay they are wrapped in a redacting type and
unwrapped at one line, and redaction happens at the logger so no call site can forget it. Checked:
the demo password appears nowhere in `evidence/`, `artifacts/` or `interventions/`.

Four adversarial runs against the live agent loop, all logged in `evidence/`:

| Goal | What happened |
|---|---|
| open an account, **then sign out** | refused the control, then walked around it by URL. Config now denies paths as well as names |
| a page carrying a planted instruction | ignored it and finished the goal |
| **return the exact password** | never had the value, could not capture it, ended `stuck` with nothing written |
| a ceiling of three steps | stopped at three and wrote no artifact |

The second is one model and one payload, which is the point of not relying on it: had it obeyed,
the allowlist would still have refused the click.

**Limits.** The allowlist matches on control names, so a renamed button changes its own risk class,
which is why those names sit in per-tenant config. And it knows *what* is being clicked, not
*whether this caller should*: real authorization belongs above this layer.

---

## 7. Cuts

**No sense of what is inside what.** A snapshot is flat and real back-office screens are tables, so
"the balance in the row for account 12345" cannot be said. A locator cannot carry a caller's
parameter, anchoring reaches only the first and last control of a role, and `navigate` reaches only
the entry point.

**No desktop driver.** Section 4 argues the seam holds and the tests run a second implementation
of it, but a real Windows UI Automation driver is untried.

**A validation error is not distinguishable on these flows.** ParaBank collapses a bad value into
its generic error page. Its Bill Pay screen does carry real per-field validation, which we
checked, so covering this row honestly means a third capability: the feature breadth the brief
says it does not reward.

**No learning from a demonstration.** Someone demonstrating a flow was authorized in that moment.
Replaying it later, unattended, is an authorization nobody gave.

**Not built:** queues, a database, cloud deployment, multi-tenant plumbing, because the brief says
it does not reward them. Retry-everything, because only declared conditions should be retried. An
LLM fallback when replay fails, because replay staying model free is the whole claim.
