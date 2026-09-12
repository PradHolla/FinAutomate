# Findings

Bugs this project found in itself, and what changed. Kept because the tooling that
found them is part of the design: the fault proxy, the adversarial discovery runs, and
mutation-testing our own tests.

## Recording

**Customer data ended up inside locators.** A recorded strategy read
`text: '1234512456125671267812789...'` - every account number belonging to the customer.
It verified correctly, because on that run it genuinely was the right control.
*Fix:* selects report no text, text strategies refuse digits, and the run's own values
are excluded from every locator it writes.

**A date ended up inside a locator.** A checkpoint anchored to `09-11-2026`, the day it
was recorded. Matches on exactly one day of the application's life.
*Fix:* anchors containing a digit are never recorded whole, only their digit-free prefix.

**Checkpoints were literal text with no fallback.** Locators degraded through a ladder;
checkpoints did not, so rewording a page broke a run whose locators were all fine.
*Fix:* a checkpoint is recorded on the control that appeared, addressed by the same
ladder. Two of three transitions now carry a vendor element id, which no rebrand moves.

**A control could only be anchored from above.** Anything that was the last of its role
before the next text had no anchor at all, and recorded with a single strategy.
*Fix:* anchor from below as well. One nav link went from 1 rung to 6.

**A capability was named after one run's values.** `open_new_savings_account...` for a
capability whose account type is a parameter. A name is the first thing anyone believes.
*Fix:* title, description and id are generalized before they are written.

## Replay

**Declared recovery had never once run.** Recovery was consulted only when a *control*
could not be found. Every navigation here is followed by a checkpoint, so an expired
session always arrived as "the confirmation never appeared" and was filed as a hard
failure. Found by the fault proxy, which exists because the app will not expire a
session on request.
*Fix:* declared conditions are consulted on a checkpoint miss too.

**A checkpoint matched the wrong screen and the run reported success.** The loan
checkpoint's fourth rung was "the link after *Status:*", correct on the approved screen
and also present on the refusal screen, where it matched a navigation link. The run
returned `Home` as a loan account number.
*Fix:* a declared outcome is checked after every step, whether the checkpoint held or
not. An answer from the application outranks our expectation.

**Business outcomes could only come from a dropdown.** A "record not found" page had no
path to exit 2, which is the mistake the brief calls the most common one.
*Fix:* declared business outcomes are detected from the page.

**A declared `hard_failure` detector was never read.** The artifact could say "if you
see this, the app has broken" and nothing consulted it.
*Fix:* failures are named from the artifact's own declarations.

## Safety

**Blocking a control did nothing if the page behind it was one URL away.** Asked to sign
out, the model was refused the Log Out control by name, then navigated to
`/parabank/logout.htm` and signed out anyway. Found by giving a discovery run a goal the
policy forbids.
*Fix:* config denies paths as well as control names.

**Page text was never marked as untrusted.** Screen content goes into the prompt, and in
a back-office app some of it was typed by a customer.
*Fix:* the screen is fenced and the prompt says it is data, never instructions. The
allowlist remains the actual control, since a prompt is not a security boundary.

## The agent loop

**A human pre-declared every parameter, so the model had no autonomy.** Stating a value
in both the goal and a flag meant the two could disagree, and once they did: the goal
said CHECKING, the flag said SAVINGS, and the model satisfied the gate by selecting the
parameter and immediately overwriting it. The artifact hardcoded CHECKING in three steps
and shipped a junk step whose only purpose was passing a check. That run cost 27 cents.
*Fix:* only credentials are passed in. The model declares its own typed inputs at `done`
and is held to them: a parameter it names but no step sets fails the run. The
done-rejection machinery this replaced was 88 lines.

**Prompt caching had never worked.** `cache_control` sat on the system block, which is
~1,700 tokens, and Haiku 4.5 will not cache a prefix under 4,096. No error, no cache,
silently, on every run ever made. We were also caching the fixed part while the
conversation was the part that grew.
*Fix:* cache the conversation with a breakpoint that moves forward. Cache hits are now
recorded in the evidence log, which is why this went unseen for so long.

## Running the same goals on two models

Both capabilities were recorded twice, once with Haiku 4.5 and once with Sonnet 5, from
identical goals. Two things came out of it.

**A prompt written for one model produced worse artifacts on the other.** The pause
before the irreversible step said "set every value the goal specifies, even if the field
already shows it". Haiku set only what it had missed. Sonnet read it literally and re-set
every field it had already filled, so its artifacts carried redundant steps: 10 against
Haiku's 8 for the account, 12 against 9 for the loan. Both replayed correctly, so nothing
failed and nothing would have caught it except running both.
*Fix:* the message now distinguishes a field left on a default from one already set.
Sonnet then produced 9 steps, identical to Haiku.

**Parameter names are not stable, even between two runs of the same model.** The loan's
funding input was named `from_account` in one Haiku run and `source_account` in the next,
from the same goal text. Sonnet chose `from_account` both times, which is one data point
and not a guarantee. This is the cost of letting the model design the contract: the names
are its judgement, so a caller reads them from the artifact rather than assuming them. A
calling agent does that anyway; only a human typing a CLI command is inconvenienced.

**What did not differ.** Both models parameterized the same slots, neither missed one or
invented one, and neither produced a stale reference, a policy denial or a failed action.
Sonnet cost roughly 1.8x Haiku per run.

## Process

**Tests that could not fail.** Three tests were written for one function; two passed
against a stub that ignored its arguments. Both used a fixture that already contained
the value under test.
*Fix:* break the function on purpose and confirm the test notices. Now done for every
test that guards a specific behavior.

**Verification that skipped the important part.** Four verification passes in a row were
told to skip `discover` because it costs money. The part the brief calls the heart of
the project was the one part never independently checked, and the same bias showed up in
the evidence folder and the write-up.
*Fix:* discovery is run in verification, and evidence carries six discovery runs.
