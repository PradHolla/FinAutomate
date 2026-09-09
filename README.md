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

Model access is Claude Sonnet 5 on Amazon Bedrock. Credentials come from the
standard AWS chain, so any of the usual options work:

```bash
export AWS_REGION=us-east-1
# plus AWS_PROFILE, or AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY
```

No secrets are read from or written to this repository.

## Demo path

_To be filled in as the phases land. Will be the exact two commands: run the agent
on a goal, then replay the resulting artifact._

```bash
uv run finautomate discover ...
uv run finautomate replay ...
```

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
