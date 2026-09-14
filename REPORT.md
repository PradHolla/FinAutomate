# Design write-up

*Developed with Claude Code, on Opus 5 and Sonnet 5, with subagents for implementation grunt work
and verification. The model this system drives is a separate choice, in section 1.*

The target is **ParaBank**, a real JSP banking demo from Parasoft in Docker. I did not write it, and
that matters: its inputs have no labels and its forms never change URL on submit.

This is the short version. `FINDINGS.md` has the bug behind each decision, section by section, and
`evidence/` a run behind each claim. The README maps the code.

---

## 1. Architecture

Two paths through one codebase. **Discovery** runs once: a model reads the screen, picks one action,
and I write down how to find that control again. **Replay** runs every time after, with no model in
it. Both drive the same `Surface` and the same locator code, so a recording cannot mean one thing to
each.

**The seam is a `Snapshot`**: controls (role, accessible name, form attributes) and text anchors,
flat, in reading order. Above it, pure functions testable with no browser; below it, one driver per
surface.

The agent loop is a while loop, not a framework: observe, ask for one action, check it, do it, look
again. Six decisions in it carry weight.

| Decision | Why |
|---|---|
| One action per turn | a second action from the same snapshot is chosen blind |
| Tools, not prose | no gap between what the model said and what I understood |
| A stale reference is refused, not attempted | the harness enforces what the model cannot see |
| The model never receives a secret | it names a parameter; this layer substitutes the value |
| The model designs the contract | it declares its own inputs, and a parameter no step sets fails the run |
| One pause before the point of no return | a field left on its default is not in the recording |

It never sees a screenshot. Guards it cannot talk past: 25 steps, 300 seconds, 200k tokens.

**The screen is untrusted**, since text in a back-office app was typed by a customer. It is fenced
as data, which is the cheap half; the half that counts is that the **allowlist does not care what the
model decided**.

**The model is Claude Haiku 4.5**, on the Anthropic API. Sonnet 5 is one flag away and two
capabilities were recorded on both, which is how I caught a prompt that suited one model and hurt the
other. Haiku is the default at a quarter the cost: for a system that records once and replays
forever, the cheaper model that works wins. I did not use the hosted computer-use tool, which answers
in pixel coordinates - the least durable thing you can record.

3,800 lines of Python, 147 tests, three capabilities. The last three rows exist because I shipped
the bug first. *More: The agent loop in `FINDINGS.md`.*

---

## 2. Artifact schema

A capability is a function: typed inputs, ordered steps, typed outputs, a success condition, and the
business outcomes a caller needs. Pydantic v2, frozen, unknown keys rejected, in YAML.

Two rules are enforced by the types, not documented. **No hostnames**, so one artifact serves many
institutions. **No CSS selectors and no coordinates**: controls are addressed the way a screen reader
addresses them.

**The locator bundle is the centerpiece.** Each step carries an ordered list of ways to find its
control, and replay takes the first matching *exactly one* element. Two matches is a failure, not a
coin flip. Across eight ParaBank screens there are **42 form fields and not one has an accessible
name**, so role plus name finds nothing. Lower rungs anchor to nearby text in reading order, then
the form's own `name` and `id`.

The recorder checks every strategy with the real resolver, so the two cannot disagree, and refuses
anything containing a digit. *More: Recording in `FINDINGS.md`.*

---

## 3. Determinism and error handling

Replay is deterministic because it calls no model, resolves by the recorded ladder, and waits on
stated conditions, never sleeps. Not on the network going idle: measured here, that fires *before*
the panel swap.

| Result | Exit | |
|---|---|---|
| Success | 0 | with typed outputs |
| Business outcome | 2 | the application answered, and the answer was no |
| Needs human | 3 | stopped at a step a person has to decide |
| Hard failure | 1 | step, expected, observed, screenshot |

Exit 2 matters most, and it is not one thing: a funding account the customer does not own, a loan the
bank declined, and a value the application refused are three different answers. The types stop any of
them being mistaken for a crash - a business outcome has no `screenshot` field, a failure no
`outcome` field. **An outcome quotes the application** rather than paraphrasing, because ParaBank has
four refusal wordings. All six runtime conditions the assignment lists are handled.

**Two recovery verbs.** An expired session leaves nothing behind the login page, so `restart` returns
to the entry point. A notice over an intact page has not moved the flow, so `dismiss` clears it and
retries the step. **Nothing is retried unless declared**, and those conditions live in **tenant
config, not the artifact**: a recording holds only what the model saw.

The app will not expire a session to order, so I built a fault injector. It immediately found a
declared recovery that had never once run. *More: Replay in `FINDINGS.md`.*

---

## 4. Other surfaces and other tenants

**Other surfaces.** The `Snapshot` vocabulary was chosen to exist off a browser: role, accessible
name, nearby text, reading order. Windows UI Automation and macOS Accessibility expose the same, so a
desktop driver fills in the same structure and nothing above the seam changes. The seam is not only
asserted either: the agent loop's tests drive a second, non-browser `Surface` through a whole
discovery run.

**Other tenants.** The artifact holds no hostname, so a second institution is a config change. The
fault proxy rebrands ParaBank as another bank, and the same artifact, unedited, runs:

```
click_log_in    click   [4] anchored_role  <- fallback
SUCCESS    drift warning: 8 of 8 steps needed a fallback locator
```

Every step, including the irreversible one, found its control by a rung nobody would pick first;
against the original tenant every resolves at rung 0. That difference is the argument for recording
a ladder instead of a selector.

A tenant whose flow genuinely differs needs an **override** instead, which is designed but not built
(*What I left out, in `FINDINGS.md`*).

**Drift** shows up as the rung that resolved, per step, per tenant, per run. The answer is
`discover --rerecord`, which hands the model the contract it already has and rediscovers every
locator; against ParaBank it came back identical. `version` moves only when inputs or outputs change,
so fixing drift leaves callers alone.

---

## 5. Escalation and handoff

A run needs a person in two places, and both use one mechanism: raise a request, hold the live
session, record what they do, take control back. **In replay**, when a step the recording marked
irreversible comes up unattended. **In discovery**, when the model calls `stuck`.

The discovery one is the more interesting, because what the person does becomes part of the
recording rather than only part of the log. Their click is matched back to a control on the screen
they were handed and recorded as a step with a full locator ladder, through the same verified path
the model's own steps take. If it matches anything other than exactly one control, nothing is
written: a recording with a step missing fails halfway through, having already done half the job.

**They also classify what they did.** `resolve --handled --risky` records that step as one a person
must be present for, and every unattended replay stops there from then on - a human teaching the
system its own policy, on machinery that already existed.
`evidence/discovery-8fd30e32f8/` is a real one: policy forbade the agent the final button, a person
pressed it and marked it risky, and the two replays beside it show the capability running attended
and stopping unattended.

**Who is in control is an explicit lease**: `agent`, `human` or `none`, in a file rather than memory,
because the worker and the operator are different processes. The request carries the step, why it
stopped, a screenshot, and what was on screen.

**The person gets the same live session**, and the page reports their clicks and field changes into
the same log the machine writes to. A bank cannot have a gap reading "a human did something here".

**Two decisions, kept apart.** `--approve` means the automation may act; `--handled` means the person
acted instead, so the step is skipped. Collapsing those loses what an audit cares about. Control
returns to the agent on every path, rejection included.

`evidence/replay-86ece1cfad/` is the replay side, driven by hand: the step the person did shows no
locator, because none was used.

---

## 6. Safety

**An allowlist in config, checked before every action**, in discovery and replay: a permitted path
prefix, permitted actions, controls never to touch, controls that create or move something.

**Config decides what is risky, not the model.** An agent that reports "this one is fine" about
itself is the control that fails when it matters. **Risk belongs to the control**: clicking is not
dangerous, clicking the button that opens an account is.

**Secrets** never reach the model in discovery. At replay they are wrapped in a redacting type,
unwrapped at one line, and redacted at the logger.

**I attacked it four times** with real discovery runs: a forbidden goal, a planted instruction on the
page, a goal asking for a secret it never had, and a ceiling it could not finish inside. One found a
real hole, now closed. *All four: What I found by attacking it, in `FINDINGS.md`.*

**Limits.** It matches on control names, so a renamed button changes its own risk class, which is
why those names sit in per-tenant config. And it knows *what* is clicked, not *whether this caller
should*: real authorization belongs above this layer.

---

## 7. Cuts

- **No sense of what is inside what.** A snapshot is flat, so "the balance in the row for account
  12345" cannot be said.
- **No desktop driver.** The seam has a second implementation, but not a Windows one.
- **No learning from a demonstration.** Someone demonstrating a flow was authorized in that moment;
  replaying it later, unattended, is an authorization nobody gave.
- **Not built:** queues, a database, cloud deployment, multi-tenant plumbing, retry-everything, an LLM
  fallback when replay fails.

*What each costs, and why I drew the line there: What I left out, and why, in `FINDINGS.md`.*
