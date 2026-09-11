# Design write-up

Target: **ParaBank**, a real JSP/servlet banking demo from Parasoft, run in Docker. We
did not write it, which is the point — it has unlabeled inputs and a submit that never
navigates, and both shaped the design. Every figure below was measured, not remembered.

---

## 1. Architecture

Two paths through one codebase. **Discovery** runs once: a model reads the screen, picks
one action, and we record how to find that control again. **Replay** runs forever after,
with no model in it. Both drive the same `Surface` and resolve locators with the same
code, so a recording cannot mean one thing to the recorder and another to the replayer.

One process, files on disk. No queue, database, or cloud — the brief does not reward
them and nothing here needs them.

**The seam is a `Snapshot`**: a flat list of controls (role, accessible name, form
attributes) and text anchors, each with a position in reading order. Above that line,
pure functions testable with no browser. Below it, one driver per surface.

**The model never sees a screenshot** — it gets the same text list replay gets. A model
that picks targets from pixels answers in pixels, and pixel coordinates are the least
durable thing you can put in a recording.

About 3,800 lines of Python, 98 tests, two recorded capabilities.

---

## 2. Artifact schema

A capability is a function: typed inputs, ordered steps, typed outputs, a success
condition, and the business outcomes a caller needs. Pydantic v2, `extra="forbid"`,
frozen, written as YAML so a person can read it.

Two rules the *types* enforce rather than document. **No hostnames** — `entry` is
relative and a validator rejects any scheme, because the base URL is tenant config and
that is what lets one artifact serve many institutions. **No CSS selectors or
coordinates** — controls are addressed the way a screen reader addresses them.

**The locator bundle is the centerpiece.** Each step carries ordered ways to find its
control, and replay takes the first matching *exactly one* element. Two matches is a
failure, not a coin flip: picking one of two candidates is how automation quietly does
the wrong thing.

The ladder is not decoration. Across eight ParaBank screens: **42 form fields, none with
an accessible name.** Role-plus-name, where every tutorial starts, cannot address a
single input here. So lower rungs anchor to the nearest text in reading order — "the
textbox after *Username*" — and below those sit the form's own `name` and `id`.

The recorder proposes strategies and **verifies each by running the production resolver**
on the same snapshot, so recorder and replayer cannot disagree. Anything carrying a digit
is refused: a date or an account number in a locator matches one run and nothing else.

The application's runtime conditions are deliberately not in here. See §3.

---

## 3. Determinism & error handling

Determinism comes from three things: no model calls, locators resolved by the recorded
ladder, and every wait on a declared condition rather than a sleep. We measured that this
app's "network idle" fires *before* its panel swap lands, so waiting on the network reads
the old screen.

| | exit | |
|---|---|---|
| Success | 0 | with typed outputs |
| Business outcome | 2 | the application answered, and the answer was no |
| Needs human | 3 | stopped at a step a person must decide |
| Hard failure | 1 | step, expected, observed, screenshot |

Two is the one that matters, and the loan capability shows both flavours. Fund from an
account the customer does not own: `FUNDING_ACCOUNT_ID_NOT_AVAILABLE`, the caller got it
wrong. Ask for more than they can cover and the bank refuses: `LOAN_DENIED`. Both are
answers. The types stop either being mistaken for a failure — a business outcome has no
`screenshot` field, a failure has no `outcome` field.

**A declared answer outranks a checkpoint**, a rule bought the hard way. The loan
checkpoint's fourth rung was "the link after *Status:*" — correct on the approved screen,
and present on the refusal screen too, where it matched a navigation link. The run
reported success with `Home` as a loan account number. An outcome is now checked after
every step whether the checkpoint held or not: a checkpoint asks "did what I expected
appear?", an outcome asks "what did the application say?"

**Nothing is retried unless it was declared**, with a detector and a bounded count.
Retry-on-anything is how automation hammers a production system while looking busy. Those
conditions live in **tenant config, not the artifact** — a recording holds only what the
model saw, and nothing breaks on a healthy app, so no recording will ever carry a session
timeout. Asking the model to invent one turns a guess into a retry policy aimed at a bank.

**Testing this needed a fault injector**, because the app will not expire a session or go
slow on request. `finautomate proxy` sits in front of it and misbehaves to order; the
driver reaches it by changing the base URL, already a per-tenant setting. It found a real
bug immediately: recovery was consulted only when a *control* could not be found, and
every navigation here is followed by a checkpoint — so an expired session always arrived
as "the confirmation never appeared" and was filed as a hard failure. The declared
recovery had never run. `evidence/replay-c7e26cfb69/` is it working.

**Drift is reported even on success.** A step that used to resolve at rung 0 and now
resolves at rung 2 is the earliest warning the page moved.

---

## 4. Heterogeneity & multi-tenant

**Other surfaces.** `Snapshot`'s vocabulary was chosen to exist somewhere other than a
browser: role, accessible name, nearby text, reading order. Windows UI Automation and
macOS Accessibility both expose a control tree with roles and names, so a desktop driver
fills the same structure and nothing above the line changes — including waiting, because
checkpoints poll `observe()` rather than a browser primitive. Reading order rather than
pixels survives a restyle, and is the one ordering a desktop tree also has.

**Other tenants.** The artifact holds no hostname, so a second institution is a config
change. The fault proxy rewrites ParaBank into a differently-branded bank — new name, new
labels, a different word on the submit button — and the same artifact, unedited, runs:

```
type_text_username       type    [2] anchored_role  <- fallback
click_log_in             click   [4] anchored_role  <- fallback
click_open_new_account_2 click   [5] anchored_role  <- fallback
...
SUCCESS    drift warning: 8 of 8 steps needed a fallback locator
```

Every step, including the irreversible one, found its control by a rung nobody would pick
first. Against the original tenant every step resolves at rung 0, and that difference
*is* the drift signal. Per-tenant runtime conditions come from the same place: the second
tenant's `SESSION_EXPIRED` detector looks for a field labeled "User ID", because that is
what that bank calls it.

---

## 5. Escalation & handoff

**Detecting stuck.** Discovery: the model calls `stuck`, or a guard trips (25 steps, 300
seconds, 200k tokens). Replay: a hard failure, or an irreversible step with nobody
watching.

**Who is in control** is an explicit lease — `agent`, `human`, or `none` — written to a
file rather than held in memory, because the worker and the operator are different
processes. The request carries which capability, which step, why it stopped, a
screenshot, and the controls on screen in the form the model sees them.

**The person gets the same live session.** While they hold it, the page reports their
clicks and field changes through a passive listener into the same log as everything the
machine did — a bank cannot have a gap reading "a human did something here". Password
fields report `<redacted>`.

**Two decisions, kept apart.** `--approve` means the automation may act; `--handled`
means the person did it themselves and the step is skipped. Collapsing them loses the
difference between a machine acting with permission and a human acting instead of one,
which is what an audit cares about. Control returns to the agent on **every** path
including rejection — a lease left with someone who walked away is how a run hangs
forever.

`evidence/replay-0d5fb2c4f4/` is a real one: held at the irreversible step, a person
clicked the button themselves, the click is in the record, control came back. The
operator console is a CLI, which the brief allows: the mechanism is real, the interface
is mocked.

---

## 6. Safety

**An allowlist in config, checked before every action**, in discovery and replay:
permitted path prefix, permitted action types, controls the agent must never touch
(`Admin Page`, `Log Out`), and controls that create or move something (`Open New
Account`, `Transfer`, `Send Payment`, `Withdraw`, `Apply Now`).

**Config is the authority on risk, not the model.** Letting the model classify its own
actions would usually work — but an agent that self-reports "this one is fine" is exactly
the control that fails when it matters. Its opinion is a hint, checked against the rule.
The rule wins.

**Risk is a property of the control, not the action.** "Click" is not dangerous. Clicking
the button that opens a bank account is. So the rule matches on what the control is
called, which is what a person reading an audit log recognizes. A risky step in an
unattended run does not execute; it stops and asks.

**Secrets.** The model cannot type a password even if it wants to: it calls `type_secret`
naming a parameter and never receives the value. At replay, secrets are wrapped in a
redacting type and unwrapped at one line — the call into the browser. Redaction happens
at the logger, not per call site, so it cannot be forgotten. Verified: the demo password
appears nowhere in `evidence/`, `artifacts/`, or `interventions/`.

**Limits.** The allowlist matches on control names, so a renamed button changes its own
risk class — the brittleness the ladder absorbs, and the reason those names sit in
per-tenant config. And the policy knows *what* is being clicked, not *whether this caller
should*. Real authorization belongs in the calling agent, above this layer.

---

## 7. Cuts

**A `read` can only address a form control.** A value in a table cell — a balance, a
status, the reason a loan was refused — has no locator, because a `Snapshot` splits the
world into controls you can act on and text you cannot touch. That is why `LOAN_DENIED`
reports *that* the bank refused and not *why*. Give anchors a handle and a strategy that
addresses them: a few hours, and the first thing we would do next.

**No containment**, the single missing idea behind most of this list. A snapshot is flat,
in reading order; real back-office screens are tables, and "the balance in the row for
account 12345" needs to know what sits inside what. We cut the `Scope` type early because
a flat snapshot genuinely lacks that information and faking it would look like scoping
without being scoping. Three consequences: a locator cannot carry a caller's parameter,
so "click the row they named" is not expressible; nearest-neighbour anchoring reaches
only the first and last control of a role, so `click_request_loan` records with a single
strategy; and `navigate` can only reach the entry point, so parameterized routes are not
built.

An ordinal rung — "the third link after X" — would rescue that menu link. Insert one menu
item and every ordinal below it silently shifts, and a hard failure naming the step beats
quietly clicking the wrong entry.

**Mocked at a clean seam, as the brief allows.** The operator console is a CLI; the
control-transfer model behind it is real. There is no desktop driver — §4 argues the seam
would hold, and that is the one claim here with no command behind it.

**No approval gate, so no learning from demonstrations.** We capture what a person does
during a handover, and turning that into a capability is a short step from machinery
already here. We stopped deliberately: someone demonstrating a flow was authorized in
that moment, and replaying it unattended later is an authorization nobody gave. That
needs a draft-to-approved state first.

**Not built at all:** queues, a database, cloud deployment, multi-tenant plumbing — the
brief does not reward them. Retry-everything, because only declared conditions should be
retried. And an LLM fallback when replay fails, because replay staying model-free is the
entire claim.
