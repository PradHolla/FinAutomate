# finautomate

Computer-use automation for legacy back-office applications that have no API.

An LLM drives the real UI once to work out how to do a job. What it learned is saved as
a typed, versioned capability file. After that, the job is replayed the same way every
time with no model involved, and a person can be brought in when the system cannot
safely finish on its own.

* `REPORT.md` is the design write-up.
* `FINDINGS.md` lists the bugs this project found in itself, and what changed.
* `evidence/` holds one real run per outcome, with its own index.
* `artifacts/` holds the two capabilities. **Both were recorded by an LLM driving
  the real UI**, never written by hand. Each one names the run that produced it in
  its `recorded:` block, and that run's log is in `evidence/`.

## Setup

Needs Python 3.14 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run playwright install chromium
```

Start the target app. ParaBank is a real JSP banking demo from Parasoft, standing in for
a bank's back-office system:

```bash
docker run -d -p 8080:8080 --name parabank parasoft/parabank
# check it: http://localhost:8080/parabank/
```

Everything below assumes it is running on port 8080.

## Configuration

Discovery drives the model, so it needs a key:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

**Replay needs no key at all**, which is the point of the system: the model works out how
to do a job once and is never used to do it again. So a replay costs nothing and runs
offline from the model, while a discovery run costs a few cents.

Haiku 4.5 is the default; `--model sonnet` switches. A run costs about three cents and
the CLI prints what it used, including how much came from cache.

No secrets are read from or written to this repository.

## The demo path

Three commands: reset the app, record a capability, replay it.

```bash
# 1. put the demo data back
uv run finautomate reset

# 2. let the model work out how to do the job, once
uv run finautomate discover \
  "Open a new SAVINGS account funded from account 12345, and return the new account number" \
  --param username=john --secret password=demo

# 3. run it again from the recording, with no model involved
uv run finautomate reset
uv run finautomate replay artifacts/open_new_account_funded_from_account.yaml \
  --param username=john \
  --param account_type=SAVINGS \
  --param funding_account=12345 \
  --secret password=demo \
  --attended
```

Step 2 costs about three cents and opens a real account, so reset between runs. Step 3
costs nothing and needs no key.

**Only credentials are passed in.** The model reads `SAVINGS` and `12345` out of the
goal, decides which of the values it entered a caller should be able to change, and
names them itself. Look at `inputs:` in the artifact it writes to see the contract it
designed. Parameter names are its choice, so read them off the file rather than assuming
them.

`--attended` means a person is watching. Without it the run stops before the step that
opens the account, because that step is marked irreversible. There is a section on that
below.

Add `--dry-run` to print the plan without opening a browser.

## A second capability

Both capabilities in `artifacts/` were recorded the same way: an LLM drove the real UI
once, and we wrote down what worked. Neither was written by hand.

```bash
# recorded by the model, exactly like the first one
uv run finautomate reset
uv run finautomate discover \
  "Apply for a loan of 1000 with a down payment of 900 from account 12345, and return the new loan account number" \
  --param username=john --secret password=demo
```

The result of that run is also committed, so you can compare what you get against what
we got. Every artifact says which run produced it:

```yaml
recorded:
  run: discovery-d04cbd4e04
  model: claude-haiku-4-5
  goal: Apply for a loan of 1000 with a down payment of 900 from account 12345...
  evidence: evidence/discovery-d04cbd4e04
```

That evidence directory holds the whole run: every action the model chose, every policy
decision on it, and the nine steps that came out. Including a `precondition_held` event,
where the model tried to click Apply Now before setting every parameter and the harness
refused.

Now replay it, with no model involved:

```bash
uv run finautomate reset
uv run finautomate replay artifacts/apply_for_loan_with_down_payment.yaml \
  --param username=john --secret password=demo \
  --param loan_amount=1000 --param down_payment=900 \
  --param from_account=12345 --attended
```

That one is approved and returns the new loan account number. Ask for more than the
customer can cover and the bank refuses:

```bash
uv run finautomate reset
uv run finautomate replay artifacts/apply_for_loan_with_down_payment.yaml \
  --param username=john --secret password=demo \
  --param loan_amount=900000 --param down_payment=1 \
  --param from_account=12345 --attended
```

```
BUSINESS_OUTCOME
  LOAN_DENIED: The bank declined the loan request.
```

Exit 2, not exit 1. The application considered the request and gave an answer. That is a
result the caller asked for, not a failure to wake anybody up about.

This one capability can produce **two different** business outcomes, and they are not the
same thing. Pass `--param from_account=99999`, an account the customer does not
own, and you get `FROM_ACCOUNT_NOT_AVAILABLE`. That is the caller getting it wrong.
The refusal above is the bank weighing an application and saying no. Both are answers.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | success, outputs returned |
| 1 | hard failure, something is broken |
| 2 | a business outcome, the application answered and the answer was no |
| 3 | held at a step that needs a person |

Two is deliberately neither success nor failure. "That account is not available to this
customer" is an answer the caller needs, not a crash to page someone about.

To see exit 1, stop the app with `docker stop parabank` and run a replay, or use the
injected app error described below.

## Handing the session to a person

Some steps should not run without a person. `click_open_new_account_2` opens a real bank
account, so the artifact marks it irreversible and an unattended run stops there.

That is what `--attended` has been skipping past. Drop it, and add a wait instead:

```bash
uv run finautomate reset
uv run finautomate replay artifacts/open_new_account_funded_from_account.yaml \
  --param username=john --secret password=demo \
  --param account_type=SAVINGS --param funding_account=12345 \
  --wait-for-human 300 --headed
```

The run stops, prints the request, and waits. **The browser stays open on the same
page.** That is the point. The person gets the session the automation was using, not a
fresh one.

In a second terminal:

```bash
uv run finautomate interventions   # what is waiting, and why. It prints the id.

uv run finautomate resolve <id> --approve --operator you   # the automation may do it
uv run finautomate resolve <id> --handled --operator you   # you did it yourself
uv run finautomate resolve <id> --reject  --operator you --note "not today"
```

`--approve` and `--handled` are deliberately different. One is the machine acting with
permission. The other is a person acting instead of the machine. An audit of a bank's
systems cares which, so we do not collapse them into "continue".

If you pick `--handled`, do the step in the browser first: click **Open New Account**
yourself. The page reports your clicks and field changes into the same evidence log as
everything the automation did, so there is no gap in the record. Passwords are never
recorded, only the fact that a password field changed.

Control goes back to the automation on every path, including rejection. Then the run
finishes and reports.

Without `--wait-for-human`, the run raises the request and exits 3 immediately. That is
the right behavior for an unattended queue: tell the caller a person is needed rather
than block.

`evidence/replay-0d5fb2c4f4/` is a real one, driven by hand. The `intervention.json`
there holds the whole record: which step, why it stopped, what was on screen, who
decided, and the click they made while they held the session.

## Breaking it on purpose

The app will give us a business outcome and a hard failure whenever we ask. It will not
expire a session, and it will not go slow. So the third class of failure we claim to
handle, the recoverable one, had never actually run.

`finautomate proxy` sits between the browser and the app and misbehaves to order. The
driver reaches it by pointing at a different base URL, which is already a per-tenant
setting, so nothing in the system under test changes or knows.

Each scenario needs two terminals. Start the proxy in one:

```bash
uv run finautomate proxy --rules config/faults/session-expiry.yaml
```

Run against it in the other:

```bash
uv run finautomate reset
uv run finautomate replay artifacts/open_new_account_funded_from_account.yaml \
  --param username=john --secret password=demo \
  --param account_type=SAVINGS --param funding_account=12345 \
  --base-url http://localhost:8888 --attended
```

Swap the rules file to change what breaks:

| Rules file | What it does | What should happen |
|---|---|---|
| `config/faults/session-expiry.yaml` | drops the session cookie once, mid flow | the run detects it, signs in again, finishes. Exit 0 |
| `config/faults/slow.yaml` | 6s on the page load, 8s on the account call | still succeeds, in about 15s. Exit 0 |
| `config/faults/app-error.yaml` | breaks the call that opens the account | `HARD_FAILURE ... APP_ERROR: The application showed its internal error page.` Exit 1 |

The capability itself says nothing about session timeouts or error pages, and it should
not. A discovery run can only record what it saw, and nothing went wrong while it was
recording. Those conditions are declared in `config/parabank.yaml` under `outcomes:` and
merged in when replay loads the artifact. They are facts about the application, not about
one flow through it.

The session-expiry rule fires **once**. A fault that fires forever only proves the retry
limit works. One that fires once proves the retry works.

The app-error rule answers without forwarding, so nothing is created upstream and it is
safe to run against real data. The other two do open a real account, so reset afterwards.

## The same recording at a second institution

`config/faults/tenant-b.yaml` rewrites the app's wording into another bank's: different
name, different labels, a different word on the submit button. Field names and element
ids are left alone, because those belong to the vendor, not the bank.

```bash
uv run finautomate proxy --rules config/faults/tenant-b.yaml --port 8889
```

```bash
uv run finautomate replay artifacts/open_new_account_funded_from_account.yaml \
  --param username=john --secret password=demo \
  --param account_type=SAVINGS --param funding_account=12345 \
  --config config/tenant-b.yaml --attended
```

Same artifact, unedited. It succeeds, and it tells you which steps needed a lower rung of
the ladder to get there:

```
  type_text_username               type     [2] anchored_role  <- fallback
  type_secret_password             type     [1] anchored_role  <- fallback
  click_log_in                     click    [4] anchored_role  <- fallback
  click_open_new_account           click    [2] anchored_role  <- fallback
  select_account_type              select   [3] anchored_role  <- fallback
  select_funding_account           select   [1] anchored_role  <- fallback
  click_open_new_account_2         click    [5] anchored_role  <- fallback
  read_new_account_number          read     [3] field_id  <- fallback

SUCCESS in 524ms
  new_account_number = '13566'
  drift warning: 8 of 8 steps needed a fallback locator
  evidence : evidence/replay-ab6cda16d6
```

Every step, including the irreversible one, found its control by a rung nobody would have
picked first. Two strings are left un-renamed, and the rules file says which and why.
Both are cases where the *locator* ladder runs out, not cases where a checkpoint is
brittle.

Run the same artifact against `config/parabank.yaml` and every step resolves at rung 0.
That difference is the whole argument for recording a ladder instead of a selector.

## Development

```bash
uv run ruff check .      # lint
uv run ruff format .     # format
uv run mypy              # types
uv run pytest            # tests
```

## A note on scope

ParaBank also exposes SOAP and REST services. They are ignored on purpose. The assignment
is about the long tail of applications that have no API at all, and it says API
integration is the preferred path and out of scope. This system drives the UI only.
