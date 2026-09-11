# finautomate

Computer-use automation for legacy back-office applications that have no API.

An LLM drives the real UI once to work out how to do a job. What it learned is saved
as a typed, versioned capability file. After that the job is replayed deterministically
with no model in the decision loop, and a human can be brought in when the system
cannot safely finish on its own.

> Status: in progress. See `project.md` for the build plan and `REPORT.md` for the
> design write-up.

## Setup

Requires Python 3.14 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run playwright install chromium
```

Start the target application (ParaBank, a real JSP banking app used here as a
stand-in for a bank's back-office system):

```bash
docker run -d -p 8080:8080 --name parabank parasoft/parabank
# http://localhost:8080/parabank/
```

## Configuration

Discovery uses the Anthropic API. One environment variable:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

`--model sonnet` (the default) or `--model haiku`. The artifact in `artifacts/` was
recorded with Haiku 4.5, because it is cheaper and it works: about five cents a run
against roughly eight for Sonnet 5. Not a free win - Haiku needs more turns, and
context grows with every turn, so the first Haiku run actually cost *more* than
Sonnet until a precondition check cut the turn count. Cost per task is not cost per
token.

**Replay needs no key at all.** That is the point of the system: the model is used
once to work out how to do a job, and never again to do it. If you only want to see
a capability run, skip this section entirely.

No secrets are read from or written to this repository.

## Demo path

Reset the target to a known state, discover a capability, then replay it.

```bash
# 1. put the demo data back
uv run finautomate reset

# 2. let the model work out how to do the job, once
uv run --env-file .env finautomate discover \
  "Open a new SAVINGS account funded from account 12345, and return the new account number" \
  --param username=john \
  --param account_type=SAVINGS \
  --param funding_account_id=12345 \
  --secret password=demo \
  --expect-output new_account_number

# 3. run it again from the recording, with no model involved
uv run finautomate reset
uv run finautomate replay artifacts/open_new_account_funded_from_account.yaml \
  --param username=john \
  --param account_type=SAVINGS \
  --param funding_account_id=12345 \
  --secret password=demo \
  --attended
```

Step 2 costs a few cents and creates a real account, so reset between runs.
Step 3 costs nothing and needs no API key.

`--attended` says a person is watching. Without it the run stops before the step
that opens the account, because that step is marked irreversible.

Add `--dry-run` to print the plan without opening a browser.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | success, outputs returned |
| 1 | hard failure - something is broken |
| 2 | a business outcome - the application answered, and the answer was no |
| 3 | held at a step that needs a person |

Two is deliberately neither. "That account is not available to this customer" is an
answer the caller needs, not a crash to page someone about.

Try it: pass `--param funding_account_id=99999`, an account the customer does not
own, and compare with stopping the app entirely (`docker stop parabank`).

## Breaking it on purpose

The application will give us a business outcome and a hard failure whenever we ask.
It will not expire a session, and it will not go slow. So the third class of failure
we claim to handle - recoverable - had never actually run.

`finautomate proxy` sits between the browser and the application and misbehaves to
order. The driver reaches it by pointing at a different base URL, which is already a
per-tenant setting, so nothing in the system under test changes or knows.

Each scenario is two terminals. Start the proxy in one:

```bash
uv run finautomate proxy --rules config/faults/session-expiry.yaml
```

and run against it in the other:

```bash
uv run finautomate reset
uv run finautomate replay artifacts/open_new_account_funded_from_account.yaml \
  --param username=john --secret password=demo \
  --param account_type=SAVINGS --param funding_account_id=12345 \
  --base-url http://localhost:8888 --attended
```

| Rules file | What it does | What should happen |
|---|---|---|
| `config/faults/session-expiry.yaml` | drops the session cookie once, mid flow | the run detects it, signs in again, finishes. Exit 0 |
| `config/faults/slow.yaml` | 6s on the page load, 8s on the account call | still succeeds, in about 15s. Exit 0 |
| `config/faults/app-error.yaml` | breaks the call that opens the account | `HARD_FAILURE ... APP_ERROR: The application showed its internal error page.` Exit 1 |

The capability itself says nothing about session timeouts or error pages, and it
should not: a discovery run can only record what it saw, and nothing went wrong
while it was recording. Those conditions are declared in `config/parabank.yaml`
under `outcomes:` and merged in when replay loads the artifact. They are facts about
the application, not about one flow through it.

The session-expiry rule fires **once**. A fault that fires forever only proves the
retry limit works; one that fires once proves the retry works.

The app-error rule answers without forwarding, so nothing is created upstream and it
is safe to run against real data. The other two do open a real account - reset after.

### The same recording at a second institution

`config/faults/tenant-b.yaml` rewrites the application's wording into another bank's:
different name, different labels, a different word on the submit button. Field names
and element ids are left alone, because those belong to the vendor, not the bank.

```bash
uv run finautomate proxy --rules config/faults/tenant-b.yaml --port 8889
```

```bash
uv run finautomate replay artifacts/open_new_account_funded_from_account.yaml \
  --param username=john --secret password=demo \
  --param account_type=SAVINGS --param funding_account_id=12345 \
  --config config/tenant-b.yaml --attended
```

Same artifact, unedited. It succeeds, and it says which steps needed a lower-tier
locator to get there:

```
  type_text_username               type     [2] anchored_role  <- fallback
  type_secret_password             type     [1] field_name  <- fallback
  click_log_in                     click    [4] anchored_role  <- fallback
  click_open_new_account           click    [2] anchored_role  <- fallback
  select_account_type              select   [3] anchored_role  <- fallback
  select_funding_account           select   [2] field_id  <- fallback
  click_open_new_account_2         click    [6] anchored_role  <- fallback
  read_new_account_number          read     [3] field_id  <- fallback

SUCCESS in 590ms
  new_account_number = '13566'
  drift warning: 8 of 8 steps needed a fallback locator
  evidence : evidence/replay-de452df63d
```

Every step, including the irreversible one, found its control by a rung nobody
would have chosen first. Two strings are left un-renamed and the rules file says
which and why - both because a *locator* ladder runs out there, not because a
checkpoint is brittle.

Run the same artifact against `config/parabank.yaml` and every step resolves at
tier 0. That difference is the whole argument for recording a ladder instead of a
selector.

## Development

```bash
uv run ruff check .      # lint
uv run ruff format .     # format
uv run mypy              # types
uv run pytest            # tests
```

## A note on scope

ParaBank also exposes SOAP and REST services. They are ignored deliberately. The
assignment's premise is the long tail of applications that have no API at all, and
it states that API integration is the preferred path and out of scope. This system
drives the UI only.
