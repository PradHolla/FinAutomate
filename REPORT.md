# Design write-up

Target: **ParaBank**, a real JSP/servlet banking demo from Parasoft, run in Docker.
We did not write it, which is the point — it has unlabeled inputs and a submit that
never navigates, both of which shaped the design.

Every figure below was measured against the running app, not remembered.

---

## 1. Architecture

Two paths through one codebase.

**Discovery** runs once: a model reads the screen, picks one action, and we record how
to find that control again. **Replay** runs forever after, with no model in it. Both
drive the same `Surface` and use the same locator resolution, so a recording cannot
mean one thing to the recorder and another to the replayer.

One process, files on disk. No queue, database, or cloud — the brief says it does not
reward that, and nothing here needs it.

**The seam is a `Snapshot`**: a flat list of controls (role, accessible name, form
attributes) and text anchors, each with a position in reading order. Everything above
that line is pure functions over that structure, testable with no browser. Everything
below is one driver per surface.

**The model never sees a screenshot.** It gets the same text list replay gets. A model
that picks targets from pixels answers in pixels, and pixel coordinates are the least
durable thing you can put in a recording.

About 3,700 lines of Python, 94 tests.

---

## 2. Artifact schema

A capability is a function: typed inputs, ordered steps, typed outputs, a success
condition, and the business outcomes a caller needs. Pydantic v2, `extra="forbid"`,
frozen, written as YAML so a person can read it.

Two rules the *types* enforce rather than document:

- **No hostnames.** `entry` is a relative path and a validator rejects any scheme. The
  base URL is tenant config. That is what lets one artifact serve many institutions.
- **No CSS selectors, no coordinates.** Controls are addressed the way a person or a
  screen reader addresses them.

**The locator bundle is the centerpiece.** Each step carries an ordered list of ways to
find its control. Replay takes the first that matches *exactly one* element. Two
matches is a failure, not a coin flip — picking one of two candidates is how
automation quietly does the wrong thing.

The ladder is not decoration. Across eight ParaBank screens: **42 form fields, none
with an accessible name.** Role-plus-name, where every automation tutorial starts,
cannot address a single input here. So the next tier anchors to the nearest text in
reading order — "the textbox after the word *Username*" — and below that sit the
form's own `name` and `id`.

The recorder proposes strategies, then **verifies each one by running the production
resolver** on the same snapshot. Recorder and replayer cannot disagree; they are the
same code.

The application's runtime conditions are deliberately *not* in here. See §3.

---

## 3. Determinism & error handling

Determinism comes from three things: replay makes no model calls, locators resolve by
the recorded ladder, and every wait is on a declared condition rather than a sleep. We
measured that this app's "network idle" fires *before* its panel swap lands, so waiting
on the network reads the old screen.

**Four result types, four exit codes.**

| | exit | |
|---|---|---|
| Success | 0 | with typed outputs |
| Business outcome | 2 | the application answered, and the answer was no |
| Needs human | 3 | stopped at a step a person must decide |
| Hard failure | 1 | step, expected, observed, screenshot |

Two is the one that matters. "That account is not available to this customer" is an
answer the caller asked for, not a crash to page someone about. The types make them
impossible to confuse: a business outcome has no `screenshot` field, a failure has no
`outcome` field.

**Nothing is retried unless it was declared**, with a detector and a bounded attempt
count. Generic retry-on-anything is how automation hammers a production system while
looking like it is working.

Those conditions live in **tenant config, not the artifact**. A discovery run only
records what it saw, and nothing breaks on a healthy app, so no recording will ever
contain a session timeout — and asking the model to invent one turns a guess into a
retry policy aimed at a bank. It is also a fact about the *application*, not one flow
through it: every capability recorded here needs the same clause.

**Testing this needed a fault injector.** The app gives us a business outcome and a
hard failure on request, but it will not expire a session or go slow. `finautomate
proxy` sits in front of it and misbehaves to order. The driver reaches it by changing
the base URL — already a per-tenant setting — so the system under test never knows.

It found a real bug immediately: **recovery was consulted only when a control could not
be found.** Every navigation here is followed by a checkpoint, so an expired session
always arrived as "the confirmation never appeared" and was filed as a hard failure.
The declared recovery had never once run. `evidence/replay-1fd1e665ef/` is it working.

**Drift is reported even on success.** A step that used to resolve at tier 0 and now
resolves at tier 2 is the earliest warning that the page moved.

---

## 4. Heterogeneity & multi-tenant

**Other surfaces.** `Snapshot`'s vocabulary was chosen to exist somewhere other than a
browser: role, accessible name, nearby text, reading order. Windows UI Automation and
macOS Accessibility both expose a control tree with roles and names. A desktop driver
fills the same structure from a different source and nothing above the line changes —
including waiting, because checkpoints poll `observe()` rather than a browser
primitive.

Reading order rather than pixels is the deliberate part. It survives a restyle, and it
is the one ordering a desktop control tree also has.

**Other tenants.** The artifact holds no hostname, so a second institution is a config
change. We demonstrated it rather than claiming it. The fault proxy rewrites ParaBank
into a differently-branded bank — new name, new labels, a different word on the submit
button — and the same artifact, unedited, still runs:

```
type_text_username       type    [2] anchored_role  <- fallback
click_log_in             click   [4] anchored_role  <- fallback
click_open_new_account_2 click   [6] anchored_role  <- fallback
...
SUCCESS    drift warning: 8 of 8 steps needed a fallback locator
```

Every step, including the irreversible one, found its control by a rung nobody would
pick first. Against the original tenant, every step resolves at tier 0. That
difference *is* the drift signal: one run tells you it worked and tells you the page
moved.

Per-tenant runtime conditions come from the same place — the second tenant's
`SESSION_EXPIRED` detector looks for a field labeled "User ID", because that is what
that bank calls it.

---

## 5. Escalation & handoff

**Detecting stuck.** Discovery: the model calls `stuck`, or a guard trips (25 steps,
300 seconds, 200k tokens). Replay: a hard failure, or a step marked irreversible with
nobody watching.

**Who is in control** is an explicit lease — `agent`, `human`, or `none` — written to a
file, not held in memory, because the worker and the operator are different processes.
They always are in a real deployment.

The request carries which capability, which step, why it stopped, a screenshot, and
the controls on screen in the same form the model sees them.

**The person gets the same live session**, not a fresh one. While they hold it, the
page reports their clicks and field changes back through a passive listener, into the
same log as everything the machine did — a bank cannot have a gap reading "a human did
something here". Password fields report `<redacted>`.

**Two decisions, kept apart.** `--approve` means the automation may act. `--handled`
means the person did it themselves and the step is skipped. Collapsing them into
"continue" loses the difference between a machine acting with permission and a human
acting instead of one, which is what an audit cares about.

Control returns to the agent on **every** path, including rejection — the worker still
has to unwind and report, and a lease left with someone who walked away is how a run
hangs forever.

`evidence/replay-0d5fb2c4f4/` is a real one: held at the irreversible step, a person
clicked the button themselves, the click is in the record, control came back, the run
finished.

The operator console is a CLI, which the brief allows: the mechanism is real, the
interface is mocked.

---

## 6. Safety

**An allowlist in config, checked before every action**, in discovery and replay:
permitted path prefix, permitted action types, controls the agent must never touch
(`Admin Page`, `Log Out`), and controls that create or move something (`Open New
Account`, `Transfer`, `Send Payment`, `Withdraw`, `Apply Now`).

Two decisions worth defending:

**Config is the authority on risk, not the model.** Letting the model classify its own
actions would usually work — but an agent that self-reports "this one is fine" is
exactly the control that fails when it matters. Its opinion is recorded as a hint and
checked against the rule. The rule wins.

**Risk is a property of the control, not the action.** "Click" is not dangerous.
Clicking the button that opens a bank account is. So the rule matches on what the
control is called, which is also what a person reading an audit log recognizes.

A risky step in an **unattended** run does not execute. It stops and asks.

**Secrets.** The model cannot type a password even if it wants to: it calls
`type_secret` naming a parameter and never receives the value. At replay, secrets are
wrapped in a redacting type and unwrapped at one line — the call into the browser.
Redaction happens at the logger, not per call site, so it cannot be forgotten.
Verified: the demo password appears nowhere in `evidence/`, `artifacts/`, or
`interventions/`.

**Limits.** The allowlist matches on control names, so a renamed button changes its own
risk class — the same brittleness the locator ladder absorbs, and the reason those
names sit in per-tenant config. And the policy knows *what* is being clicked, not
*whether this caller should*. Real authorization belongs in the calling agent, above
this layer.

---

## 7. Cuts

*(written last — pending the decision on a second capability)*
