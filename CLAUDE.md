# CLAUDE.md

Rules for working in this repo. `project.md` is the plan; this file is how we work.

---

## 1. Look it up before you build it

**Do not implement from memory.** Break the task down, then check each piece
against a current source — official docs, the package's own metadata, the actual
running app.

This is not a style preference. Training data goes stale, and this repo depends on
fast-moving things: Playwright's API, the Anthropic SDK, model ids and pricing,
Python versions, ParaBank's real markup.

It has already paid for itself three times in this project:

| Assumption | Reality after checking |
|---|---|
| Build a custom Flask "legacy" app | ParaBank is a real one, and more credible because we didn't write it |
| Use `page.accessibility.snapshot()` for perception | Deprecated. `aria_snapshot()` has no element refs — it's an assertion tool, not a targeting tool |
| Python 3.12 is a sensible floor | 3.14 is current and every dependency supports it |

How this looks in practice:

- Before using a library API → check its current docs or PyPI metadata.
- Before claiming a platform supports a feature → check the availability table.
- Before designing against an app's UI → fetch the real page and read the markup.
- Uncertain and can't verify? **Say so out loud.** Don't paper over it.

Guessing costs more than looking. Always.

---

## 2. No slop

Both directions count as slop:

- **Under-thought:** generated code nobody can explain, copy-paste duplication,
  dead abstractions, silent `except:` blocks, `sleep()` instead of a real wait.
- **Over-built:** unrequested abstraction layers, config for things that never
  vary, a plugin system with one plugin, infrastructure the brief actively
  penalizes (queues, workers, DB, cloud deploy).

The test: **can this be defended, line by line, in an interview?** If not, it
doesn't ship. This whole repo is a work sample — every file is evidence.

Practical rules:

- Explain the design *before* writing the code, not after.
- Small, reviewable pieces. No large dumps at the end.
- If a decision is arbitrary, say it's arbitrary. Don't invent a rationale.
- Delete rather than comment out.
- An abstraction with exactly one implementation is fine **only** if the seam is
  the point (our `Surface` protocol). Otherwise it's decoration.

---

## 3. Write like a human

Everything written here — docs, comments, commit messages, REPORT.md, chat — uses
plain, simple language. Short sentences. Ordinary words.

This is not decoration. "Communication" is one of the things the brief grades, and
the reviewer is reading a stack of submissions in a row. The one they understand on
the first pass wins. Complicated writing usually hides thin thinking; clear writing
is the harder thing to fake.

- Say the thing directly. Don't warm up to it over three sentences.
- Prefer the short word. "use" not "utilize", "so" not "therefore", "we cut X"
  not "X was deprioritized".
- No academic voice, no thesis phrasing, no showing off vocabulary.
- Explain jargon the first time it appears, in half a sentence.
- **Be transparent.** If we don't know something, say we don't know. If a decision
  was a coin flip, say so. If we cut something because we ran out of time, write
  that, not a euphemism. Honest beats impressive, and it usually reads as more
  senior anyway.
- **No emojis. Anywhere.** Not in docs, not in code, not in commits, not in chat.
  Use words, or plain "yes"/"no", or nothing.
- **US English.** color, behavior, normalize, license, catalog. Not colour,
  behaviour, normalise. The reviewers are a US company.
- **Do not fake collaboration.** If a decision is yours to make, make it and explain
  the reasoning. Asking someone to "challenge" a call they lack the context to judge
  wastes their time. Ask a real question or state a real decision.

The test: would this make sense read aloud to someone who just walked in?

---

## 4. Python

- **Python 3.14**, managed with **uv**. `uv sync`, `uv run`, `uv add` — never bare
  `pip` or a hand-rolled venv.
- Dependencies and tool config live in `pyproject.toml`. A committed `uv.lock`.
- **ruff** for lint + format. **mypy** on the core modules.
- Type hints everywhere. Modern syntax: `str | None`, `list[str]`, no `typing.List`.
- Pydantic v2 for anything that gets serialized, validated, or written to disk.
  The artifact schema *is* the deliverable — it must be a real type.
- `pathlib`, not `os.path`. `dataclass`/Pydantic, not dicts-as-records.
- Structured logging to JSONL. No `print()` outside the CLI's own output.
- Custom exceptions that mean something. Never a bare `except`.
- **Pure functions where the logic lives** — locator resolution, error
  classification, policy checks. They should be testable with no browser running.

### Testing

Test what carries risk, not what's easy:

- locator bundle resolution and fallback order
- error classification (business outcome vs recoverable vs hard failure)
- policy/allowlist enforcement
- artifact schema validation and round-tripping

Skip: coverage targets, tests for getters, browser-dependent integration tests we'd
have to babysit.

---

## 5. Subagents

Two are defined in `.claude/agents/`, both on Sonnet:

| Agent | For | Tools |
|---|---|---|
| `implementer` | Building something whose approach is already decided | read, write, edit, run |
| `tester` | Checking whether a claim about this repo is actually true | read and run only - it cannot edit |

`tester` deliberately has no editing tools. An agent that fixes what it finds cannot
tell you how bad the finding was.

### Deciding, in order

Work down this list and stop at the first line that fits. Earlier lines win.

1. **Does it need our accumulated context or our judgment?** The ParaBank findings,
   the rubric reading, why a decision went the way it did. Then keep it here. A cold
   agent does not have any of that and will confidently invent a substitute.
2. **Is it a question about whether something works?** Then `tester`. Including -
   especially - work we just finished ourselves.
3. **Is the approach decided, and does "done" mean a command that passes?** Then
   `implementer`. Give it the contract: file paths, signatures, input and output
   shapes, and the exact command that must run clean.
4. **Would writing the instructions cost more than writing the code?** Roughly: under
   100 lines, no research needed, no separate verification. Then do it here.

Rule 1 beats rule 3 whenever both look true. Size is a cost question; context is a
correctness one.

A **fork** is the third option and it is neither of the agents above. It inherits
this whole conversation and runs on our model, so it is not a cost saving - it is a
context saving, for work that would otherwise dump a pile of tool output into this
thread. Reach for it when the task needs our history *and* would be noisy.

### Say which one you picked

**Whenever a task was big enough that someone might reasonably ask "why didn't you
delegate that?", answer it in one line before starting.** Name the rule above.

This is not bureaucracy. The failure this prevents already happened: a component
sitting squarely in rule 3 got built by hand with no reason given, and the first the
reader knew of it was when they went looking. A silent call reads as no call at all.

### What a subagent starts with

`CLAUDE.md` at every level loads automatically into a subagent, so do not spend
instructions restating what is already here.

What does **not** carry over: this conversation, the files already read, the results
of anything already run. That gap is what the instructions have to fill, and it is
the whole reason a cold agent guesses where we would look something up.

### Non-negotiable

The brief says we own everything submitted and must be able to defend any part of it
in detail. So a subagent's report is evidence, not a verdict. Read the diff, and run
the thing yourself when it matters. If nobody has genuinely read a file, it does not
ship, however clean it looks.

---

## 6. Git

- **No co-authored-by trailer.** No "Generated with Claude Code" footer.
  (`includeCoAuthoredBy: false` is already set globally — keep it that way.)
- **Commit messages are one line. No body, no bullet list, no footer.**
  Say what changed, plainly. Aim for under ~60 characters.
  - Good: `add locator bundle resolution with fallback tiers`
  - Good: `fix session timeout detection in replay`
  - Bad: multi-paragraph messages, bullet summaries, generated-by footers
- Never commit secrets. AWS credentials come from the environment.
- Don't run git commands unless asked.

---

## 7. This project specifically

- Read `project.md` before starting a phase. Tick the boxes as they're done.
- **Model:** Claude Sonnet 5, direct Anthropic API. `model="claude-sonnet-5"`,
  client is a bare `Anthropic()` reading `ANTHROPIC_API_KEY`. No Bedrock, no AWS.
- **Never put a CSS selector in an artifact.** Locator bundles only.
- **Never record a scraped `href` or any session-bound URL.** Record intent —
  "click the link named X". Entry-point URLs are fine; harvested ones are not.
- Replay never calls the model. That is the whole point of the system.
- Fake data only. No real credentials, no real PII, ever.
- The brief penalizes scaling infrastructure. No AWS, no queues, no database.
  Single process, files on disk.

---

## 8. Verify an edit landed

A scripted find-and-replace that matches nothing exits 0 and changes nothing. Twice
now, an edit "applied" and the code was untouched - both times because a formatter
had reflowed the lines being searched for. Once the method existed and was never
called; once a guard was written and never ran.

After any scripted edit, grep for something the new code contains. Passing tests are
not proof: the code that was supposed to change may simply not be there.

---

## 9. When errors pile up, stop patching

Two or three failures in a row on the same task means the approach is wrong, not
that three unrelated bugs happened. Stop, step back, and look it up before writing
another fix.

This is the same rule as section 1, but it is easiest to break under pressure:
patching symptom by symptom feels like progress. It usually is not.

What this looked like in practice: the discovery loop failed three times running -
a 400 from parallel tool calls, a policy denial from mismatched names, then a
30-second timeout from a double click. The third one I "fixed" with a wait. Reading
Anthropic's own agent-design guidance took two minutes and named the actual pattern:
a dedicated tool should **reject** an action whose target changed since the model
last looked, rather than attempt it. That is a better fix than the wait, and it was
written down the whole time.
