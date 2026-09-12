# finautomate

Computer-use automation for legacy back-office applications that have no API.

An LLM drives the real UI once to work out how to do a job. What it learned is saved as a
typed, versioned **capability** file. After that the job runs from that file every time,
with no model involved, and a person can be brought in when the system cannot safely
finish on its own.

* `REPORT.md` is the design write-up.
* `FINDINGS.md` lists the bugs this project found in itself, and what changed.
* `evidence/` holds one real run per outcome, with its own index.
* `artifacts/` holds the two capabilities. **Both were recorded by an LLM driving the real
  UI**, never written by hand. Each names the run that produced it in its `recorded:`
  block, and that run's log is in `evidence/`.

## A note on scope

ParaBank, the target app, also exposes SOAP and REST services. We ignore them on purpose.
The assignment is about applications that have no API at all, and says API integration is
the preferred path and out of scope. This system drives the UI only.

## Setup

Needs Python 3.14 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run playwright install chromium
```

Start the target app. ParaBank is a real JSP banking demo from Parasoft, standing in for a
bank's back-office system:

```bash
docker run -d -p 8080:8080 --name parabank parasoft/parabank
```

**Give it about a minute.** Tomcat is still starting and the page refuses connections until
it is up. Check with <http://localhost:8080/parabank/>.

If you stop it later, start it again with `docker start parabank`. Running `docker run`
a second time fails on the container name.

Everything below assumes it is on port 8080.

### Credentials

`john` / `demo` is ParaBank's built-in demo customer, already seeded. No registration step.
Account `12345` is one of his. These are fake demo values that ship with the app, and they
are the only credentials anywhere in this repo.

### An API key, for discovery only

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

**Replay needs no key**, which is the point of the system: the model works out how to do a
job once and is never used to do it again. A replay costs nothing and runs with no model.
A discovery run costs about two cents.

Both capabilities are already committed, so **every replay command in this file works
without a key.** You can skip every `discover` command and still exercise the whole system.

## Reading the output

Every replay prints one line per step, and those lines carry the main argument, so it is
worth decoding them once:

```
  click_log_in            click    [0] role_name
  type_text_username      type     [2] anchored_role  <- fallback
```

A step does not record one selector. It records an **ordered list of ways to find its
control**, best first. We call that the ladder, and each entry a rung. Replay tries them in
order and uses the first that matches *exactly one* control on screen.

* `[0]` is the rung that worked. `[0]` is the first choice.
* `role_name`, `anchored_role`, `field_id` say *how* it was found: by role and visible name,
  by the nearest text in reading order, or by the form's own element id.
* `<- fallback` means the first choice missed and a lower rung caught it. Not an error, but
  the earliest sign the page has drifted.
* `drift warning: 8 of 8 steps needed a fallback locator` is that same signal, counted.

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

**Only credentials are passed in.** The model reads `SAVINGS` and `12345` out of the goal,
decides which of the values it entered a caller should be able to change, and names them
itself. Look at `inputs:` in the artifact to see the contract it designed. The names are its
choice, so read them off the file rather than assuming them.

**Step 2 will not overwrite the capability we ship.** A capability's filename comes from its
goal, so a second run of the same goal lands on the same file. Rather than replace a file
other things may already be calling, it writes `open_new_account_funded_from_account.new.yaml`
beside it and prints the flag to use if you did mean to replace it. So step 3 above replays
the committed artifact. To replay what you just recorded, point step 3 at the `.new.yaml`
file. There is a section on re-recording below.

Step 2 costs about two cents and opens a real account, so reset between runs. Step 3 costs
nothing.

`--attended` means a person is watching, so steps marked irreversible are allowed to run.
Without it the run stops before the step that opens the account. There is a section on that
below.

Add `--dry-run` to see what a capability takes and what it would do, without opening a
browser:

```
$ uv run finautomate replay artifacts/open_new_account_funded_from_account.yaml --dry-run
open_new_account_funded_from_account v1: Open a new {{account_type}} account funded ...
  takes: username, password*, account_type, funding_account
         * never logged or written to disk
  type_text_username        type    Username field = {{username}}
  ...
  click_open_new_account_2  click   Open New Account button  [RISKY - needs a person]
  read_new_account_number   read    New account number
  success: the link that appeared
  returns: new_account_number (from read_new_account_number)
```

It works with no parameters at all, which is how you find out what a capability takes. Give
it parameters and it checks them too, so a typo is caught in a second rather than halfway
through a real run.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | success, outputs returned |
| 1 | hard failure, something is broken |
| 2 | a business outcome: the application answered, and the answer was no |
| 3 | held at a step that needs a person |

Two is deliberately neither success nor failure. "That account is not available to this
customer" is an answer the caller needs, not a crash to page someone about.

## A second capability

Both capabilities were recorded the same way: an LLM drove the real UI once and we wrote
down what worked.

```bash
uv run finautomate reset
uv run finautomate discover \
  "Apply for a loan of 1000 with a down payment of 900 from account 12345, and return the new loan account number" \
  --param username=john --secret password=demo
```

Every artifact names the run that produced it:

```yaml
recorded:
  run: discovery-f889b045f3
  model: claude-haiku-4-5
  goal: Apply for a loan of 1000 with a down payment of 900 from account 12345...
  evidence: evidence/discovery-f889b045f3
  supersedes: discovery-d04cbd4e04
```

That evidence directory holds the whole run: every action the model chose, every policy
decision on it, and the nine steps that came out. Including a `paused_before_risky` event,
where the model reached the irreversible step and the harness made it check its work first.

Replay it, with no model involved:

```bash
uv run finautomate reset
uv run finautomate replay artifacts/apply_for_loan_with_down_payment.yaml \
  --param username=john --secret password=demo \
  --param loan_amount=1000 --param down_payment=900 \
  --param from_account=12345 --attended
```

That one is approved and returns the new loan account number.

### The two kinds of "no"

Ask for more than the customer can cover and the bank refuses:

```bash
uv run finautomate reset
uv run finautomate replay artifacts/apply_for_loan_with_down_payment.yaml \
  --param username=john --secret password=demo \
  --param loan_amount=900000 --param down_payment=1 \
  --param from_account=12345 --attended
```

```
BUSINESS_OUTCOME
  LOAN_DENIED: The bank declined the loan request. We cannot grant a loan in that
  amount with your available funds.
```

Exit 2, not exit 1. The application considered the request and gave an answer.

The second sentence is the bank's words, not ours. ParaBank has four refusal wordings
depending on which of funds and down payment fell short, and the run reads whichever one came
back off the page. Ask instead for a down payment you cannot cover:

```bash
uv run finautomate reset
uv run finautomate replay artifacts/apply_for_loan_with_down_payment.yaml \
  --param username=john --secret password=demo \
  --param loan_amount=100 --param down_payment=900000 \
  --param from_account=12345 --attended
```

```
  LOAN_DENIED: The bank declined the loan request. You do not have sufficient funds
  for the given down payment.
```

This one capability produces **two different** business outcomes, and they are not the same
thing. Pass `--param from_account=99999`, an account the customer does not own, and you get
`FROM_ACCOUNT_NOT_AVAILABLE`. That is the caller getting it wrong. The refusals above are the
bank weighing an application and saying no. Both are answers.

To see exit 1, stop the app with `docker stop parabank` and run a replay, or use the injected
app error described below. Remember `docker start parabank` afterwards.

## What the agent is allowed to do

`config/parabank.yaml` holds the policy, and every action is checked against it before it
runs, during discovery and during replay:

* `allowed_path_prefix` - the agent cannot navigate outside the application.
* `denied_control_names` - controls it must never touch, such as the admin page and Log Out.
* `denied_paths` - pages it must never reach, because blocking a control does nothing if the
  page behind it is one URL away. A real discovery run found that hole.
* `risky_control_names` - controls that create or move something. These are what make a step
  `risky` in the recording, which is what stops an unattended replay.

The model's own opinion about risk is recorded as a hint. The config decides.

## Handing the session to a person

`click_open_new_account_2` opens a real bank account, so the artifact marks it risky and an
unattended run stops there. That is what `--attended` has been skipping past. Drop it, and add
a wait instead:

```bash
uv run finautomate reset
uv run finautomate replay artifacts/open_new_account_funded_from_account.yaml \
  --param username=john --secret password=demo \
  --param account_type=SAVINGS --param funding_account=12345 \
  --wait-for-human 300 --headed
```

`--wait-for-human 300` holds the session open for 300 **seconds**. `--headed` shows the
browser window, which you need here because you are about to use it. Runs are headless
otherwise.

The run stops, prints the request, and waits. **The browser stays open on the same page.** The
person gets the session the automation was using, not a fresh one.

In a second terminal:

```bash
uv run finautomate interventions   # what is waiting, and why. It prints the id.

uv run finautomate resolve <id> --approve --operator you   # the automation may do it
uv run finautomate resolve <id> --handled --operator you   # you did it yourself
uv run finautomate resolve <id> --reject  --operator you --note "not today"
```

`--approve` and `--handled` are deliberately different. One is the machine acting with
permission. The other is a person acting instead of the machine. An audit of a bank's systems
cares which, so we do not collapse them into "continue".

**Pick the one that matches what you actually did.** If you clicked **Open New Account**
yourself in the browser, say `--handled`, and the step is skipped. Say `--approve` after
clicking it and the run will try to click a button that is no longer on screen, and fail with
exit 1. The waiting run notices the screen move and prints a reminder when it does.

`--operator` records who decided, in the audit trail. It defaults to `operator`. `--note` adds
a reason and works with any of the three decisions.

`--reject` means do not proceed. The run reports `NEEDS_HUMAN` with the note you gave and exits
3. Control returns to the automation on every path, including rejection, because a lease left
with someone who walked away is how a run hangs forever.

If you pick `--handled`, do the step in the browser first. The page reports your clicks and
field changes into the same evidence log as everything the automation did, so there is no gap
in the record. Passwords are never recorded, only the fact that a password field changed.

Without `--wait-for-human`, the run raises the request and exits 3 immediately. That is the
right behavior for an unattended queue: tell the caller a person is needed rather than block.

`evidence/replay-275b89d0f4/` is a real one, driven by hand. Its `intervention.json` holds the
whole record: which step, why it stopped, what was on screen, who decided, and the click they
made while they held the session.

## Re-recording when the application changes

A capability records how the screens looked on the day it was made. When they move, you
re-record. The problem is that the model names the parameters, so a fresh recording of the same
flow would hand every caller a new set of names for no reason.

`--rerecord` hands it the names the capability already has:

```bash
uv run finautomate reset
uv run finautomate discover \
  "Open a new SAVINGS account funded from account 12345, and return the new account number" \
  --param username=john --secret password=demo \
  --rerecord artifacts/open_new_account_funded_from_account.yaml
```

The id is kept. The parameter names are given to the model with an instruction to reuse them
for the same values, and to add a new one only for a value they do not cover. Every locator,
checkpoint and step is discovered again, which is the entire reason to re-record.

What happens to the version depends on whether the contract actually moved:

| Result | Version | File |
|---|---|---|
| same inputs and outputs | unchanged | same file, overwritten |
| inputs or outputs differ | bumped | `<id>.v2.yaml`, the old file left alone |

So fixing locator drift costs callers nothing, and a real contract change leaves the old
version on disk for anything pinned to it. The run says which happened:

```
contract: unchanged, still v1
recorded 8 steps to artifacts/open_new_account_funded_from_account.yaml
```

Each re-recording names the run it replaced in `recorded.supersedes`, so a version chain can be
followed back.

Without the flag, an existing capability is never overwritten: the new recording is written
beside it as `<id>.new.yaml` and the run tells you which flag you wanted.

## Breaking it on purpose

The app will give us a business outcome and a hard failure whenever we ask. It will not expire
a session, and it will not go slow. So the recoverable class of failure had never actually run.

`finautomate proxy` sits between the browser and the app and misbehaves to order. The driver
reaches it by pointing at a different base URL, which is already a per-tenant setting, so
nothing in the system under test changes or knows.

Each scenario needs two terminals. The proxy **runs in the foreground and blocks** until you
stop it with ctrl-c. Start it in one terminal:

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

To try the next scenario, **stop the proxy with ctrl-c first** and start it again with a
different rules file. Leaving it running makes the next one fail on a busy port.

| Rules file | What it does | What should happen |
|---|---|---|
| `session-expiry.yaml` | drops the session cookie once, mid flow | detects it, signs in again, finishes. Exit 0 |
| `slow.yaml` | 6s on the page load, 8s on the account call | still succeeds, in about 15s. Exit 0 |
| `app-error.yaml` | breaks the call that opens the account | `HARD_FAILURE ... APP_ERROR`. Exit 1 |
| `interstitial.yaml` | puts a notice over the page, once | clears it and retries the step it was on. Exit 0. Takes about 30s |
| `permission-denied.yaml` | refuses the screen to this user | `HARD_FAILURE ... PERMISSION_DENIED`. Exit 1 |

The capability itself says nothing about session timeouts or error pages, and it should not. A
discovery run can only record what it saw, and nothing went wrong while it was recording. Those
conditions are declared in `config/parabank.yaml` under `outcomes:` and merged in when replay
loads the artifact. They are facts about the application, not about one flow through it.

The session-expiry rule fires **once**. A fault that fires forever only proves the retry limit
works. One that fires once proves the retry works.

`app-error` and `permission-denied` never reach the point of creating anything. The other three
do open a real account, so run `uv run finautomate reset` afterwards.

## The same recording at a second institution

`config/faults/tenant-b.yaml` rewrites the app's wording into another bank's: different name,
different labels, a different word on the submit button. Field names and element ids are left
alone, because those belong to the vendor, not the bank.

```bash
uv run finautomate proxy --rules config/faults/tenant-b.yaml --port 8889
```

`--port` moves the proxy off its default of 8888, so this can run alongside the fault scenarios
above. In the other terminal:

```bash
uv run finautomate reset
uv run finautomate replay artifacts/open_new_account_funded_from_account.yaml \
  --param username=john --secret password=demo \
  --param account_type=SAVINGS --param funding_account=12345 \
  --config config/tenant-b.yaml --attended
```

`--config` selects the tenant. It defaults to `config/parabank.yaml`. A tenant file holds the
base URL, the policy, and the application conditions in that institution's own words: tenant B's
session detector looks for a field labeled "User ID", because that is what that bank calls it.

Same artifact, unedited. It succeeds, and it tells you which steps needed a lower rung to get
there:

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
  evidence : evidence/replay-40a629e583
```

Every step, including the irreversible one, found its control by a rung nobody would have picked
first. Run the same artifact against `config/parabank.yaml` and every step resolves at rung 0.
That difference is the whole argument for recording a ladder instead of a selector.

Two strings in the rules file are left un-renamed, and it says which and why. Both are cases
where the locator ladder runs out, not cases where a checkpoint is brittle.

## Command reference

`uv run finautomate --help`, and `--help` on any command.

### `discover` - drive the UI with a model and save a capability

| Flag | Default | |
|---|---|---|
| `--param name=value` | | a value to give the run. Repeatable |
| `--secret name=value` | | a value the model is never shown. Repeatable |
| `--rerecord PATH` | | re-record an existing capability, keeping its id and names |
| `--model` | `haiku` | `haiku` or `sonnet`. Not model ids |
| `--config PATH` | `config/parabank.yaml` | which tenant's policy and base URL to use |
| `--entry PATH` | `/parabank/index.htm` | page to start from |
| `--out DIR` | `artifacts` | where to write the capability |
| `--headed` | off | show the browser |

### `replay` - run a capability, with no model

| Flag | Default | |
|---|---|---|
| `--param name=value` | | a declared input. Repeatable |
| `--secret name=value` | | a declared secret input. Repeatable |
| `--attended` | off | a person is watching, so risky steps may run |
| `--wait-for-human N` | `0` | hold the session open N seconds at a risky step |
| `--dry-run` | off | print the contract and the plan, touch nothing |
| `--config PATH` | `config/parabank.yaml` | which tenant |
| `--base-url URL` | from config | override it, to route through the proxy |
| `--headed` | off | show the browser |

### `reset` - put ParaBank back to a known state

| Flag | Default | |
|---|---|---|
| `--clean` | off | strip the demo data instead of restoring it |
| `--config PATH` | `config/parabank.yaml` | which tenant |

### `proxy` - misbehave in front of the app, on purpose

| Flag | Default | |
|---|---|---|
| `--rules PATH` | required | which faults to inject |
| `--port N` | `8888` | port to listen on |

### `interventions` and `resolve` - the operator's side

`interventions` lists what is waiting. `resolve <id>` hands control back with exactly one of
`--approve`, `--handled` or `--reject`, plus optional `--operator who` (default `operator`) and
`--note why`.

## Development

```bash
uv run ruff check .      # lint
uv run ruff format .     # format
uv run mypy              # types
uv run pytest            # tests
```
