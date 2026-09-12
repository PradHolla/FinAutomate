# Design write-up

The target is **ParaBank**, a real JSP banking demo from Parasoft, run in Docker. We did not write
it, and that matters: its inputs have no labels and its forms never change URL on submit.

This is the short version. `FINDINGS.md` carries the bug behind each decision, section by section,
and `evidence/` a run behind each claim.

---

## 1. Architecture

Two paths through one codebase. **Discovery** runs once: a model reads the screen, picks one
action, and we write down how to find that control again. **Replay** runs every time after, with
no model in it. Both drive the same `Surface` and resolve locators with the same code, so a
recording cannot mean one thing to each.

**The seam is a `Snapshot`**: controls (role, accessible name, form attributes) and text anchors,
flat, in reading order. Above it, pure functions testable with no browser. Below it, one driver
per surface.

The agent loop is a while loop, not a framework: observe, ask for one action, check it, do it,
look again. Six decisions carry weight.

| Decision | Why |
|---|---|
| One action per turn | a second action from the same snapshot is chosen blind |
| Tools, not prose | no gap between what the model said and what we understood |
| A stale reference is refused, not attempted | the harness enforces what the model cannot see |
| The model never receives a secret | it names a parameter; we substitute the value |
| The model designs the contract | it declares its own inputs, and a parameter no step sets fails the run |
| One pause before the point of no return | a field left on its default is not in the recording |

It never sees a screenshot either: a model that picks targets from pixels answers in pixels.
Guards it cannot talk past: 25 steps, 300 seconds, 200k tokens.

**The screen is untrusted**, since text in a back-office app was typed by a customer. It is fenced
and the prompt says everything inside is data. That is the cheap half; the half that counts is
that the **allowlist does not care what the model decided**.

3,900 lines of Python, 143 tests, two capabilities, each recorded on two models. The last three
rows of that table exist because we shipped the bug first. *More: The agent loop, and Running the
same goals on two models, in `FINDINGS.md`.*

---

## 2. Artifact schema

A capability is a function: typed inputs, ordered steps, typed outputs, a success condition, and
the business outcomes a caller needs. Pydantic v2, frozen, unknown keys rejected, in YAML.

Two rules are enforced by the types, not documented. **No hostnames**: `entry` is relative and a
validator rejects any scheme, which is what lets one artifact serve many institutions. **No CSS
selectors and no coordinates**: controls are addressed the way a screen reader addresses them.

**The locator bundle is the centerpiece.** Each step carries an ordered list of ways to find its
control, and replay takes the first matching *exactly one* element. Two matches is a failure, not a
coin flip. Across eight ParaBank screens there are **42 form fields and not one has an accessible
name**, so role plus name finds nothing here. Lower rungs anchor to nearby text in reading order;
below those sit the form's own `name` and `id`.

The recorder checks every strategy with the real resolver, so recorder and replayer cannot
disagree, and refuses anything containing a digit. *More: Recording in `FINDINGS.md`, including the
customer account numbers we once wrote into a locator.*

---

## 3. Determinism and error handling

Replay is deterministic because it calls no model, resolves by the recorded ladder, and waits on
stated conditions, never sleeps. Not on the network going idle: measured here, that fires *before*
the panel swap lands.

| Result | Exit | |
|---|---|---|
| Success | 0 | with typed outputs |
| Business outcome | 2 | the application answered, and the answer was no |
| Needs human | 3 | stopped at a step a person has to decide |
| Hard failure | 1 | step, expected, observed, screenshot |

Exit 2 matters most, and the loan capability produces both kinds: a funding account the customer
does not own, and a refused application. The types stop either being mistaken for a crash: a
business outcome has no `screenshot` field, a failure has no `outcome` field. **An outcome quotes
the application**, because ParaBank has four refusal wordings and paraphrasing picks one.

**Two recovery verbs.** An expired session leaves nothing behind the login page, so `restart`
returns to the entry point. A notice over an intact page has not moved the flow, so `dismiss`
clears it and retries the step. **Nothing is retried unless declared**, with a detector and bounded
attempts. Those conditions live in **tenant config, not the artifact**: a recording holds only what
the model saw, and nothing breaks on a healthy app.

The app will not expire a session to order, so we built a fault injector. `finautomate proxy`
misbehaves on request, and immediately found our declared recovery had never once run. *More:
Replay in `FINDINGS.md`.*

---

## 4. Other surfaces and other tenants

**Other surfaces.** The `Snapshot` vocabulary was chosen to exist off a browser: role, accessible
name, nearby text, reading order. Windows UI Automation and macOS Accessibility expose the same, so
a desktop driver fills in the same structure and nothing above the seam changes, waiting included,
because checkpoints poll `observe()`. And the seam is not only asserted: the agent loop's tests
drive a second, non-browser `Surface` through a whole discovery run, unchanged.

**Other tenants.** The artifact holds no hostname, so a second institution is a config change. The
fault proxy rebrands ParaBank as another bank, and the same artifact, unedited, still runs:

```
click_log_in             click   [4] anchored_role  <- fallback
SUCCESS    drift warning: 8 of 8 steps needed a fallback locator
```

Every step, including the irreversible one, found its control by a rung nobody would pick first;
against the original tenant every step resolves at rung 0. That difference is the whole argument
for recording a ladder instead of a selector.

**Overrides**, for a tenant the ladder cannot absorb: a sparse patch keyed by step id, replacing a
step's locator, checkpoint or risk, never adding or reordering steps. That would be a different
flow, and patches that grow into second recordings are not reviewable. Designed, not built.

**Drift** shows up as the rung that resolved, per step, per tenant, per run. The answer is
`discover --rerecord`, which hands the model the contract it already has and rediscovers every
locator; against ParaBank it came back identical. `version` moves only when inputs or outputs
change, so fixing drift leaves every caller alone.

---

## 5. Escalation and handoff

A run is stuck when the model calls `stuck`, a guard trips, replay hits a hard failure, or an
irreversible step comes up unattended.

**Who is in control is an explicit lease**: `agent`, `human` or `none`, in a file rather than in
memory, because the worker and the operator are different processes. The request carries the
capability, the step, why it stopped, a screenshot, and what was on screen.

**The person gets the same live session.** While they hold it, the page reports their clicks and
field changes into the same log the machine writes to, because a bank cannot have a gap reading "a
human did something here". Password fields report `<redacted>`.

**Two decisions, kept apart.** `--approve` means the automation may act; `--handled` means the
person acted instead, so the step is skipped. Collapsing those loses what an audit cares about.
Control returns to the agent on every path, including rejection.

`evidence/replay-275b89d0f4/` is a real one, driven by hand: the step the person did shows no
locator, because none was used. *More: Replay in `FINDINGS.md` - it found two bugs in its own audit
trail.*

---

## 6. Safety

**An allowlist in config, checked before every action**, in discovery and replay: a permitted path
prefix, permitted actions, controls never to touch, and controls that create or move something.

**Config decides what is risky, not the model.** An agent that reports "this one is fine" about
itself is the control that fails when it matters. **Risk belongs to the control**: clicking is not
dangerous, clicking the button that opens an account is.

**Secrets** never reach the model in discovery. At replay they are wrapped in a redacting type,
unwrapped at one line, and redacted at the logger so no call site can forget. The demo password
appears nowhere in `evidence/`, `artifacts/` or `interventions/`.

**We attacked it four times** with real discovery runs: a forbidden goal, a planted instruction on
the page, a goal asking for a secret the model never had, and a step ceiling it could not finish
inside. One found a real hole, now closed. *All four: What we found by attacking it, in
`FINDINGS.md`.*

**Limits.** The allowlist matches on control names, so a renamed button changes its own risk class,
which is why those names sit in per-tenant config. And it knows *what* is clicked, not *whether this
caller should*: real authorization belongs above this layer.

---

## 7. Cuts

- **No sense of what is inside what.** A snapshot is flat, so "the balance in the row for
  account 12345" cannot be said.
- **No desktop driver.** Section 4 argues the seam holds and the tests run a second
  implementation, but a real Windows UI Automation driver is untried.
- **A validation error is not distinguishable here.** ParaBank collapses a bad value into its
  generic error page, so covering that row needs a third capability.
- **No learning from a demonstration.** Someone demonstrating a flow was authorized in that
  moment; replaying it later, unattended, is an authorization nobody gave.
- **Not built:** queues, a database, cloud deployment, multi-tenant plumbing, retry-everything, or
  an LLM fallback when replay fails.

*What each costs and why we drew the line there: What we left out, and why, in `FINDINGS.md`.*
