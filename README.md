# finautomate

*Developed with Claude Code, on Opus 5 and Sonnet 5, with subagents for implementation grunt work
and verification. The model this system itself drives is a separate choice: Haiku 4.5, Sonnet 5 and only
while recording.*

> **Built and tested on macOS, Apple silicon.** Nothing in it is deliberately Mac-specific: uv,
> Playwright and Python 3.14 all run everywhere, and ParaBank publishes a native arm64 image as well
> as x86. But macOS is the only platform it has actually been run on, so that is the one I can
> vouch for.

Computer-use automation for legacy back-office applications that have no API.

An LLM drives the real UI once to work out how to do a job. What it learned is saved as a
typed, versioned **capability** file. After that the job runs from that file every time,
with no model involved, and a person can be brought in when the system cannot safely
finish on its own.

* `REPORT.md` is the design write-up.
* `FINDINGS.md` lists the bugs this project found in itself, and what changed.
* `evidence/` holds one real run per outcome, with its own index.
* `artifacts/` holds the three capabilities. **All were recorded by an LLM driving the real
  UI**, never written by hand. Each names the run that produced it in its `recorded:`
  block, and that run's log is in `evidence/`.

## A note on scope

ParaBank, the target app, also exposes SOAP and REST services. I ignore them on purpose.
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
control**, best first. I call that the ladder, and each entry a rung. Replay tries them in
order and uses the first that matches *exactly one* control on screen.

* `[0]` is the rung that worked. `[0]` is the first choice.
* `role_name`, `anchored_role`, `field_id` say *how* it was found: by role and visible name,
  by the nearest text in reading order, or by the form's own element id.
* `<- fallback` means the first choice missed and a lower rung caught it. Not an error, but
  the earliest sign the page has drifted.
* `drift warning: 8 of 8 steps needed a fallback locator` is that same signal, counted.

## The demo path

Every capability here follows the same three steps, and this is the first of three times you will
see them:

**record it once → ask what it takes → run it, as often as you like.**

### 1. Record it

```bash
uv run finautomate reset

uv run finautomate discover \
  "Open a new SAVINGS account funded from account 12345, and return the new account number" \
  --param username=john --secret password=demo
```

Only credentials go in. The model reads `SAVINGS` and `12345` out of the goal itself, decides which
of the values it typed a future caller should be able to change, and names them. About two cents,
and it opens a real account.

### 2. Ask what it takes

**Since the model picks the parameter names, the artifact is the only place those names exist. One
dry run reads them off.** No browser opens, nothing is touched, and you only need it once per
recording.

```bash
uv run finautomate replay artifacts/open_new_account_funded_from_account.yaml --dry-run
```

```text
open_new_account_funded_from_account v1: Open a new {{account_type}} account funded from
account {{funding_account}}, and return the new account number

  takes: username, password*, account_type, funding_account
         * never logged or written to disk

  type_text_username         type     the Username field = {{username}}
  type_secret_password       type     the Password field = {{password}}
  click_log_in               click    the Log In button
  click_open_new_account     click    the Open New Account link
  select_account_type        select   the Account Type dropdown = {{account_type}}
  select_account_fund        select   the account to fund from dropdown = {{funding_account}}
  click_open_new_account_2   click    the Open New Account button  [RISKY - needs a person]
  read_new_account_number    read     the new account number

  success: the link that appeared
  returns: new_account_number (from read_new_account_number)
```

Top to bottom: the **name and version**, the **parameters it takes** with a star on anything
secret, **every step in order**, then **how it knows it worked** and **what it hands back**.
`[RISKY - needs a person]` marks the step an unattended run stops at.

The line that matters for the next step is `takes:`.

### 3. Run it

Those four names, straight off the dry run, become the flags:

```bash
uv run finautomate reset

uv run finautomate replay artifacts/open_new_account_funded_from_account.yaml \
  --param username=john \
  --secret password=demo \
  --param account_type=SAVINGS \
  --param funding_account=12345 \
  --attended
```

```text
SUCCESS in 527ms
  new_account_number = '13566'
```

No model, no key, and it costs nothing. Run it as many times as you like; the dry run was a
one-off.

Guess a name instead of reading it and you find out immediately, before a browser opens:

```text
pay_bill_phone_account_from_account takes no parameter named ['amount']
```

`--attended` means a person is watching, so the step marked irreversible is allowed to run. Without
it the run stops there and asks. There is a section on that below.

**Add `--headed` to step 1 to watch it.** Discovery runs headless by default. With the flag a real
Chromium window opens and you can see the model work: it pauses a second or two between actions
while it looks at the screen and decides, which is the cost replay removes. Two moments are worth
catching. It tries to click **Open New Account**, gets refused, goes back to set the dropdowns it
had left on their defaults, and only then clicks again. And the password field fills in without the
model ever seeing the value, because it asked for `type_secret` by name.

### If you have no API key

Step 1 is the only one that costs anything or needs a key, and all three capabilities are already
committed in `artifacts/`. **Skip step 1 and the rest works**, here and in both sections that
follow.

### Starting from nothing

If you would rather not take my word for any of it, delete both output directories and build them
back:

```bash
rm -rf artifacts evidence
```

Recording all three costs about seven cents in total. The filenames come back the same, because a
capability's id is built from its goal with the parameters stripped out. **The parameter names may
not**, which is the whole reason step 2 exists. Recording these three afresh just now renamed two
of them: the loan's returned value went from `new_loan_account_number` to `loan_account_number`,
and the bill payment's `amount` became `bill_amount`.

One thing does not come back: `evidence/README.md` is a hand-written index naming specific run ids,
so it will describe runs that no longer exist.

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

Same three steps. Record it once:

```bash
uv run finautomate reset

uv run finautomate discover \
  "Apply for a loan of 1000 with a down payment of 900 from account 12345, and return the new loan account number" \
  --param username=john --secret password=demo
```

A dry run to find out the field names:

```bash
uv run finautomate replay artifacts/apply_for_loan_with_down_payment.yaml --dry-run
```

```text
apply_for_loan_with_down_payment v1: Apply for a loan of {{loan_amount}} with a down payment
of {{down_payment}} from account {{from_account}}, and ...

  takes: username, password*, loan_amount, down_payment, from_account
         * never logged or written to disk

  type_text_username            type     Username field = {{username}}
  type_secret_password          type     Password field = {{password}}
  click_log_in                  click    Log In button
  click_request_loan            click    Request Loan link
  type_text_loan_amount         type     Loan Amount field = {{loan_amount}}
  type_text_down_payment        type     Down Payment field = {{down_payment}}
  select_from_account           select   From account dropdown = {{from_account}}
  click_apply_now               click    Apply Now button  [RISKY - needs a person]
  read_new_loan_account_number  read     New loan account number

  success: the link that appeared
  returns: loan_account_number (from read_new_loan_account_number)
```

Then run it with those names:

```bash
uv run finautomate reset

uv run finautomate replay artifacts/apply_for_loan_with_down_payment.yaml \
  --param username=john --secret password=demo \
  --param loan_amount=1000 --param down_payment=900 \
  --param from_account=12345 --attended
```

```text
SUCCESS
  loan_account_number = '13566'
```

Every artifact names the run that produced it, so you can go from any capability back to the log of
the discovery that made it:

```yaml
recorded:
  run: discovery-5f854c4f74
  model: claude-haiku-4-5
  goal: Apply for a loan of 1000 with a down payment of 900 from account 12345...
  evidence: evidence/discovery-5f854c4f74
  supersedes: discovery-d64d0fb92c
```

This one carries a `supersedes` line because it was re-recorded over an earlier version, which is
covered further down. Both runs are in `evidence/`, so the chain can be followed back.

### The two kinds of "no"

Ask for more than the customer can cover and the bank refuses:

```bash
uv run finautomate reset

uv run finautomate replay artifacts/apply_for_loan_with_down_payment.yaml \
  --param username=john --secret password=demo \
  --param loan_amount=900000 --param down_payment=1 \
  --param from_account=12345 --attended
```

```text
BUSINESS_OUTCOME
  LOAN_DENIED: The bank declined the loan request. We cannot grant a loan in that
  amount with your available funds.
```

Exit 2, not exit 1. The application considered the request and gave an answer.

That second sentence is the bank's words, not mine. ParaBank has four refusal wordings depending on
which of funds and down payment fell short, and the run reads whichever one came back off the page.
Ask instead for a down payment you cannot cover:

```bash
uv run finautomate replay artifacts/apply_for_loan_with_down_payment.yaml \
  --param username=john --secret password=demo \
  --param loan_amount=100 --param down_payment=900000 \
  --param from_account=12345 --attended
```

```text
  LOAN_DENIED: The bank declined the loan request. You do not have sufficient funds
  for the given down payment.
```

This one capability produces **two different** business outcomes, and they are not the same thing.
Pass `--param from_account=99999`, an account the customer does not own, and you get
`FROM_ACCOUNT_NOT_AVAILABLE`. That is the caller getting it wrong. The refusals above are the bank
weighing an application and saying no. Both are answers.

To see exit 1, stop the app with `docker stop parabank` and run a replay, or use the injected app
error described below. Remember `docker start parabank` afterwards.

## A third capability, and the last kind of "no"

There is a third kind of no: the application reading a value the caller supplied and refusing it.
ParaBank's Bill Pay screen is where that happens, because it validates each field rather than
funneling everything into one error page.

Same three steps. Record it once:

```bash
uv run finautomate reset

uv run finautomate discover \
  "Pay a bill of 50 to City Power, 1 Main St, Springfield, IL 62701, phone 5551234567, account 54321, from account 12345, and return the amount that was paid" \
  --param username=john --secret password=demo
```

A dry run to find out the field names. There are eleven of them this time, so this is the step you
would least want to guess at:

```bash
uv run finautomate replay artifacts/pay_bill_phone_account_from_account.yaml --dry-run
```

```text
pay_bill_phone_account_from_account v1: Pay a bill of {{bill_amount}} to {{payee_name}},
{{payee_address}}, {{payee_city}}, {{payee_state}} {{payee_zip}}, ...

  takes: username, password*, bill_amount, payee_name, payee_address, payee_city,
         payee_state, payee_zip, payee_phone, payee_account, from_account
         * never logged or written to disk

  type_text_username         type     Username field = {{username}}
  type_secret_password       type     Password field = {{password}}
  click_log_in               click    Log In button
  click_bill_pay             click    Bill Pay link
  type_text_payee_name       type     Payee Name field = {{payee_name}}
  type_text_address          type     Address field = {{payee_address}}
  type_text_city             type     City field = {{payee_city}}
  type_text_state            type     State field = {{payee_state}}
  type_text_zip_code         type     Zip Code field = {{payee_zip}}
  type_text_phone            type     Phone # field = {{payee_phone}}
  type_text_account          type     Account # field = {{payee_account}}
  type_text_verify_account   type     Verify Account # field = {{payee_account}}
  type_text_amount           type     Amount field = {{bill_amount}}
  select_from_account        select   From account # dropdown = {{from_account}}
  click_send_payment         click    Send Payment button  [RISKY - needs a person]
  read_amount_paid           read     Amount paid

  success: the text that appeared
  returns: amount_paid (from read_amount_paid)
```

Worth a second look at two lines. `type_text_account` and `type_text_verify_account` both take
`{{payee_account}}`, so the account number and its confirmation can never disagree. And the amount
is called `bill_amount`, not `amount` - an earlier recording of this same goal called it `amount`,
which is exactly why you read the names rather than remember them.

Then run it:

```bash
uv run finautomate reset

uv run finautomate replay artifacts/pay_bill_phone_account_from_account.yaml \
  --param username=john --secret password=demo \
  --param payee_name="City Power" --param payee_address="1 Main St" \
  --param payee_city=Springfield --param payee_state=IL --param payee_zip=62701 \
  --param payee_phone=5551234567 --param payee_account=54321 \
  --param bill_amount=50 --param from_account=12345 --attended
```

```text
SUCCESS
  amount_paid = '$50.00'
```

That value is a table cell, not a form field. Reading it at all is why a `read` can reach text as
well as controls.

Now pass `--param bill_amount=abc` instead:

```text
BUSINESS_OUTCOME
  VALIDATION_ERROR: The application rejected a value the caller supplied.
  Please enter a valid amount.
```

Exit 2 again, and the second sentence is the application's. This is a business outcome rather than a
failure because the application read the argument and answered: nothing is broken, no person is
needed, and the caller passes a different value and it works.

Those, plus the loan refusal and the fault scenarios further down, are all six runtime conditions
the assignment lists.

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
cares which, so I do not collapse them into "continue".

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

`evidence/replay-86ece1cfad/` is a real one, driven by hand. Its `intervention.json` holds the
whole record: which step, why it stopped, what was on screen, who decided, and the click they
made while they held the session.

## When the model gets stuck while recording

A person can be brought into a **discovery** run too, and this is the more interesting half: what
they do becomes part of the recording, not just part of the log.

`config/parabank-human-commit.yaml` is a tenant whose policy forbids the agent from committing an
account opening at all. It may fill the form; a person presses the button. Run it with a wait:

```bash
uv run finautomate reset

uv run finautomate discover \
  "Open a new SAVINGS account funded from account 12345, and return the new account number" \
  --param username=john --secret password=demo \
  --config config/parabank-human-commit.yaml \
  --wait-for-human 600 --headed
```

The model logs in, opens the form and fills both dropdowns. Then it reaches the button, is refused,
and calls `stuck`. The run holds the browser open on that screen and prints a request.

**Click the button yourself**, wait for the confirmation, then in another terminal:

```bash
uv run finautomate resolve <id> --handled --operator you --risky
```

`--risky` is you saying: *this one is irreversible, a person should be here every time.* Without it
the step is recorded as ordinary. Either way, your click is matched back to a control on the screen
you were handed and written into the recording as a real step with a full locator ladder - the same
verified path the model's own steps go through.

The result is a capability with your step in it:

```
click_open_new_account_2   click   risk=risky   <-- a person, every time
```

Replay it unattended and it stops right there and asks for somebody. Replay it with `--attended` and
it runs the whole flow. The classification you gave on the command line is now part of the
capability, permanently.

`evidence/discovery-8fd30e32f8/` is a real one, with the capability it produced sitting next to the
log that produced it.

**If the click cannot be matched to exactly one control, nothing is written.** A recording with a
step missing does not fail cleanly later; it fails halfway through, having already done half the
job. So the run says which action it could not place and writes no capability at all.

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
followed back. The loan capability is one: its `recorded:` block above names
`discovery-d64d0fb92c`, and both that run and the one that replaced it are in `evidence/`. The
other two are first recordings and have no `supersedes`.

That re-record is also the honest test of this feature. It was handed the existing contract and
rediscovered every locator from scratch, and came back with the same nine steps, the same parameter
names and the same returned value. Only the provenance block and one line of the model's own prose
differ.

Without the flag, an existing capability is never overwritten: the new recording is written
beside it as `<id>.new.yaml` and the run tells you which flag you wanted.

## Breaking it on purpose

The app gives a business outcome and a hard failure whenever asked. It will not expire
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

Those, plus the loan refusal and the bill-pay validation error above, are all six runtime
conditions the assignment lists.

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
  select_account_fund              select   [1] anchored_role  <- fallback
  click_open_new_account_2         click    [5] anchored_role  <- fallback
  read_new_account_number          read     [3] field_id  <- fallback

SUCCESS in 524ms
  new_account_number = '13566'
  drift warning: 8 of 8 steps needed a fallback locator
  evidence : evidence/replay-e1dc1ae5fe
```

Every step, including the irreversible one, found its control by a rung nobody would have picked
first. Run the same artifact against `config/parabank.yaml` and every step resolves at rung 0.
That difference is the whole argument for recording a ladder instead of a selector.

Two strings in the rules file are left un-renamed, and it says which and why. Nothing here
exhausts a ladder: every step still finds its control, just further down.

## When the ladder runs out

A ladder is deep, not infinite. `config/faults/tenant-b-redesign.yaml` is the same institution
after a fuller rebrand: it renames the marketing caption, the three panel headings and the
copyright line as well. Those are the last six rungs of the login button's ten, so all ten miss.

This is the third of the three places a person is needed. The first two are above: a risky step
with nobody watching, and the model stuck while recording. This is a replay hitting something it
cannot recover from.

```bash
uv run finautomate proxy --rules config/faults/tenant-b-redesign.yaml --port 8890
```

In the other terminal, with `--wait-for-human` so somebody is actually asked:

```bash
uv run finautomate reset
uv run finautomate replay artifacts/open_new_account_funded_from_account.yaml \
  --param username=john --secret password=demo \
  --param account_type=SAVINGS --param funding_account=12345 \
  --config config/tenant-b.yaml --base-url http://localhost:8890 \
  --attended --wait-for-human 600 --headed
```

`--base-url` points the tenant at this proxy without inventing an institution that does not
exist. `--headed` shows the browser, because a person has to be able to use it.

The run stops at the login button and prints a request. Note which verbs it offers:

```
WAITING FOR A PERSON - held at click_log_in
click_log_in failed and replay cannot get past it. Expected the Log In button;
could not find 'the Log In button'
  [0] role_name: no match
  ...
  [9] anchored_role: no match

In another terminal, choose one:

  uv run finautomate resolve <id> --handled   # you did it yourself
  uv run finautomate resolve <id> --reject    # do not proceed
```

No `--approve`. That verb means "go ahead and do it", which answers a step that has not run yet.
This one ran and failed, so there is nothing left to approve.

Click **Sign On** in the browser, then run the `--handled` line. The run carries on and finishes:

```
  click_log_in                     click    [None] None
  click_open_new_account           click    [2] anchored_role  <- fallback
  ...
SUCCESS in 29067ms
  new_account_number = '13566'
  drift warning: 9 of 10 steps needed a fallback locator
```

The step the person did shows no locator, because none was used. Everything after it resolves,
because every `type` and `select` step has a `field_id` rung and ids belong to the vendor. Only
the two clicks could have run out: ParaBank's links and submit buttons carry no id at all.

Two things this does **not** do. A failure the capability declared - `APP_ERROR`,
`PERMISSION_DENIED` - goes straight to exit 1 without asking anyone, because the author already
said that condition is terminal. And declared recoveries run first: in this run the tenant's
`SESSION_EXPIRED` fires once and restarts before anyone is called, which is why the first two
steps appear twice in the log. Drop `--wait-for-human` and the run behaves exactly as it always
did, reporting the failure and exiting 1.

`evidence/replay-120b1dc418/` is this run.

## Command reference

`uv run finautomate --help`, and `--help` on any command.

### `discover` - drive the UI with a model and save a capability

| Flag | Default | |
|---|---|---|
| `--param name=value` | | a value to give the run. Repeatable |
| `--secret name=value` | | a value the model is never shown. Repeatable |
| `--rerecord PATH` | | re-record an existing capability, keeping its id and names |
| `--model` | `haiku` | `haiku` or `sonnet`, not model ids. Haiku costs about a quarter as much and does this job; Sonnet is there because both capabilities were recorded on both |
| `--config PATH` | `config/parabank.yaml` | the tenant: base URL, policy, and the application's runtime conditions in that institution's wording |
| `--entry PATH` | `/parabank/index.htm` | the page the run opens on. Recorded into the artifact, so replay starts in the same place |
| `--out DIR` | `artifacts` | where to write the capability. Point it elsewhere to record without touching the committed ones |
| `--headed` | off | show the browser window instead of running it hidden, so you can watch the model drive |

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
| `--headed` | off | show the browser window. Needed if you plan to take over the session yourself |

### `reset` - put ParaBank back to a known state

| Flag | Default | |
|---|---|---|
| `--clean` | off | strip the demo data out rather than restoring it, leaving an empty application. Use plain `reset` to get back to a state the commands here work against |
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

## Where the code is

`REPORT.md` argues the design. This is just where to find it.

| | |
|---|---|
| `surface/` | the seam: the `Surface` protocol, the `Snapshot`, and the one driver that exists |
| `locate.py` | a recorded locator to one control on screen. Pure, no browser |
| `record.py` | a control just acted on to a locator that finds it again |
| `artifact.py` | the capability schema and the rules it refuses to break |
| `discover.py` | the agent loop |
| `replay.py` | the production path, with no model in it |
| `policy.py` | what the agent may do, checked before every action |
| `session.py`, `handover.py` | the lease, and watching a person while they hold it |
| `evidence.py` | the run log, and the one place redaction happens |
| `faultproxy.py` | a test instrument. Nothing in the production path imports it |

The three files worth reading first are `artifact.py`, because the schema is the contract
everything else serves; `locate.py`, because the locator ladder is the idea the whole design rests
on; and `surface/models.py`, which is the seam a desktop driver would plug into.

## Development

```bash
uv run ruff check .      # lint
uv run ruff format .     # format
uv run mypy              # types
uv run pytest            # tests
```
