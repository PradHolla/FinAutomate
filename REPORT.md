# Design write-up

*Developed with Claude Code, on Opus 5 and Sonnet 5, with subagents for implementation grunt work
and verification. The model this system drives is a separate choice, in section 1.*

The target is **ParaBank**, a real JSP banking demo from Parasoft in Docker. I did not write it, and
that matters: its inputs have no labels and its forms never change URL on submit.

This is the short version. `FINDINGS.md` has the bug behind each decision, section by section, and
`evidence/` a run behind each claim. The README maps the code.

---

## 1. Architecture

Two paths, one codebase. **Discovery** runs once: a model reads the screen, picks one action, and I
record how to find that control again. **Replay** runs every time after, with no model in it. Both
resolve locators through the same code, so a recording cannot mean two things.

**The seam is a `Snapshot`**: controls (role, accessible name, form attributes) and text anchors,
flat, in reading order. Above it, pure functions that need no browser. Below it, one driver per
surface.

The agent loop is a while loop, not a framework: observe, ask for one action, check it, do it, look
again. Four decisions in it carry weight.

| Decision | Why |
|---|---|
| One action per turn | a second action from the same snapshot is chosen blind |
| A stale reference is refused, not attempted | the harness enforces what the model cannot see |
| The model designs the contract | it declares its own inputs, and a parameter no step sets fails the run |
| One pause before the point of no return | a field left on its default is not in the recording |

It never sees a screenshot, and cannot talk past 25 steps, 300 seconds or 200k tokens.

**The model is Claude Haiku 4.5**, on the Anthropic API, and Sonnet 5 is one flag away. Haiku is the
default at a quarter the cost: a system that records once and replays forever should record on the
cheapest model that works. Both recorded every capability, which is how I caught a prompt that
suited one and hurt the other.

**The driver is Playwright**, for auto-waiting and one API across three engines. Not for perception:
`aria_snapshot()` carries no element references, so nothing it returns can be clicked. The extractor
is mine instead, 192 lines of JavaScript. Nor did I use the hosted computer-use tool, which answers
in pixel coordinates - the least durable thing you can record.

4,300 lines of Python, 168 tests, three capabilities. The last three table rows exist because I
shipped the bug first. *More: The agent loop, and Running the same goals on two models, in
`FINDINGS.md`.*

---

## 2. Artifact schema

A capability is a function: typed inputs, ordered steps, typed outputs, a success condition, and the
business outcomes a caller needs. Pydantic v2, frozen, unknown keys rejected, in YAML. Two rules are
enforced by the types, not by convention: **no hostnames**, so one artifact serves many institutions,
and **no CSS selectors or coordinates**, because controls are addressed the way a screen reader
addresses them.

**The locator bundle is the centerpiece.** Each step carries an ordered list of ways to find its
control, and replay takes the first one matching *exactly one* element. Two matches is a failure, not
a coin flip. Across eight ParaBank screens there are **42 form fields and not one has an accessible
name**, so role plus name finds nothing. Lower rungs anchor to nearby text in reading order, then to
the form's own `name` and `id`.

*More: Recording in `FINDINGS.md`, including how the recorder keeps itself honest.*

---

## 3. Determinism & error handling

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
bank declined, and a value the application refused are three different answers. The types keep them
apart: a business outcome has no `screenshot` field, a failure no `outcome` field.
**An outcome quotes the application** rather than paraphrasing it, because ParaBank refuses things
four different ways. All six runtime conditions the assignment lists are handled.

**Two recovery verbs.** An expired session leaves nothing behind the login page, so `restart` goes
back to the entry point. A notice over an intact page has not moved the flow, so `dismiss` clears it
and retries the step. **Nothing is retried unless declared**, and those conditions live in **tenant
config, not the artifact**: a recording holds only what the model saw.

The fault injector I built to test this found a declared recovery that had never once run.
*More: Replay in `FINDINGS.md`.*

---

## 4. Heterogeneity & multi-tenant

**Other surfaces.** The `Snapshot` vocabulary was picked to exist off a browser: role, accessible
name, nearby text, reading order. Windows UI Automation and macOS Accessibility expose all four, so a
desktop driver fills in the same structure and nothing above the seam changes. Not just asserted: the
agent loop's tests drive a second, non-browser `Surface` through a whole discovery run.

**Other tenants.** The artifact holds no hostname, so a second institution is a config change. The
fault proxy rebrands ParaBank, and the same artifact runs unedited - but every step resolves on a
fallback rung, where against the first tenant every one resolves at rung 0. That difference is the
argument for a ladder over a selector. A tenant whose flow genuinely differs needs an **override**,
designed but not built.

**Drift** shows up as which rung resolved, per step, per tenant, per run. The answer is
`discover --rerecord`, which hands the model the contract the capability already has and finds every
locator again. `version` moves only when inputs or outputs change, so fixing drift leaves callers
alone.

---

## 5. Escalation & handoff

A person is needed in three places, and all three work the same way: raise a request, hold the live
session, record what they do, take control back. **In discovery**, when the model calls `stuck`.
**In replay**, at an irreversible step with nobody watching, and at a step that failed in a way the
capability never declared.

The discovery one is the most interesting, because what the person does becomes part of the
recording, not just part of the log. Their click is matched back to a control on the screen they were
handed and recorded as a step with a full locator ladder. If it matches anything but exactly one
control, no capability is written at all.

**They also classify what they did.** `resolve --handled --risky` records that step as one a
person must be present for, and every unattended replay stops there from then on. A human teaching
the system its own policy. In `evidence/discovery-8fd30e32f8/` someone pressed a button the agent was
forbidden and marked it risky, and the two replays beside it run attended and stop unattended.

**A failure reaches a person only if nobody declared it.** A declared condition is terminal by its
author's own statement, and declared recoveries run first, so nobody is called for something the
recording could fix. In `evidence/replay-120b1dc418/` a tenant reworded one
screen further than the ladder could absorb, all ten rungs missed, and a person clicked the button
that was plainly on screen.

**Two decisions, kept apart.** `--approve` means the automation may act; `--handled` means the person
acted instead, so the step is skipped. An audit cares which. **Control is an explicit lease** -
`agent`, `human` or `none`, in a file rather than in memory, because the worker and the operator are
different processes. The person drives the same live session, and the page reports their clicks into
the log the machine writes to: a bank cannot have a gap in it reading "a human did something here".
*More: Bringing a person into a recording, in `FINDINGS.md`.*

---

## 6. Safety

**An allowlist in config, checked before every action**, in discovery and in replay: a permitted path
prefix, permitted actions, controls never to touch, controls that create or move something. **The
screen is untrusted**, because a back-office app shows text a customer typed. Fencing it as data is
the cheap half; the half that counts is that the allowlist ignores what the model made of it.

**Config decides what is risky, not the model.** An agent that reports "this one is fine" about
itself is the control that fails when it matters. **Risk belongs to the control**: clicking is not
dangerous, clicking the button that opens an account is. **Secrets** never reach the model at all; at
replay they are wrapped in a redacting type, unwrapped at one line, and redacted at the logger.

**I attacked it four times** with real discovery runs: a forbidden goal, an instruction planted in
the page, a goal asking for a secret it never had, and a ceiling it could not finish inside. One
found a real hole, now closed. *All four: What I found by attacking it, in `FINDINGS.md`.*

**Limits.** Risk matches on control names, so a renamed button changes its own class. And it knows
*what* is clicked, not *whether this caller should*: authorization belongs above this layer.

---

## 7. Cuts

- **No sense of what is inside what.** A snapshot is flat, so "the balance in the row for account
  12345" cannot be said. That is this assignment's own opening example, and the one thing here I
  would fix first.
- **No desktop driver.** The seam has a second implementation, but not a Windows one.
- **No learning from a demonstration.** Someone demonstrating a flow was authorized in that moment.
  Replaying it later, unattended, is an authorization nobody gave.
- **Not built:** queues, a database, cloud deployment, multi-tenant plumbing, retry-everything, an
  LLM fallback when replay fails.

**What I would build next**, in order: **containment**, which turns that first cut from impossible
into ordinary; **per-tenant step overrides**, so a bank whose flow differs by one screen reuses the
capability instead of re-recording it; and a **ladder depth report**, because the run that exhausted
a ladder only found out when it failed, and the rung each step lands on is already measured.

*What each cut costs, and why I drew the line there: What I left out, and why, in `FINDINGS.md`.*
