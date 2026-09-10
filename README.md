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

Discovery uses Claude Sonnet 5 through the Anthropic API. One environment variable:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

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
uv run finautomate replay ...   # Phase 4
```

Step 2 costs roughly eight cents and creates a real account, so reset between runs.
Step 3 costs nothing and needs no API key.

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
