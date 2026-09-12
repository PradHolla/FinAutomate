# Findings

Every bug this project found in itself, and what changed. It is kept because the things
that found them are part of the design: a fault-injection proxy, adversarial discovery
runs, running the same goals on two models, and breaking our own tests on purpose to see
whether they notice.

The sections follow `REPORT.md`, so a claim there can be read about here in more detail.

---

## The agent loop

**A human declared the parameters, so the model had no say.** The old command named every
field up front. That meant the goal and the flags could disagree, and once they did: the
goal said CHECKING, the flag said SAVINGS. The model satisfied the check by selecting the
parameter and immediately typing over it. The artifact hardcoded CHECKING in three steps
and carried a junk step whose only job was passing the check. That run cost 27 cents.
*Fix:* only credentials are passed in. The model declares its own inputs at `done` and is
held to them - a parameter it names but no step sets fails the run. The machinery this
replaced was 88 lines.

**The model then stopped touching the dropdowns at all.** With the check gone, and the
form already defaulting to what the goal asked for, it clicked straight through. A field
left on its default is not in the recording, so the next caller would get their own.
*Fix:* one pause before the first irreversible action, while the form is still editable.

**Re-recording renamed everything and broke every caller.** The report already said the
answer to page drift was to re-record. But a fresh run named the parameters afresh, so
anything already calling that capability broke - not because the flow changed, but
because the model picked a synonym. We had promised something that quietly broke itself.
*Fix:* `--rerecord` hands the model the names the capability already has. Everything else
is discovered again, which is the whole point. The version moves only if the contract did.

**The safe path was the longer one.** `--rerecord` is opt-in, and the filename comes from
the goal, so running discovery twice on one goal landed on the same file. Forgetting the
flag overwrote it with new names, still calling itself version 1.
*Fix:* an existing capability is never overwritten without the flag. The new recording is
written beside it, with a note naming the flag. It is not thrown away, because by then it
has been paid for.

**A re-record offered the model its own output name as a parameter.** The first version
listed inputs and outputs together. The model read the output name as something it was
meant to declare, declared it, and the run was refused because no step sets it. Found by
running it, not by reading it.
*Fix:* parameters and returned values are listed separately.

**Prompt caching had never worked.** `cache_control` sat on the system block, which is
about 1,700 tokens, and Haiku 4.5 will not cache a prefix under 4,096. No error, no
cache, on every run ever made. We were also caching the fixed part while the conversation
was the part that grew.
*Fix:* cache the conversation, with a breakpoint that moves forward. Cache hits are now
printed, which is why this went unseen for so long.

---

## Recording

**Customer data ended up inside a locator.** A recorded strategy read
`text: '1234512456125671267812789...'` - every account number belonging to that customer.
It verified correctly, because on that run it genuinely was the right control.
*Fix:* selects report no text, text strategies refuse digits, and the run's own values are
excluded from every locator it writes.

**A date ended up inside a locator.** A checkpoint anchored to `09-11-2026`, the day it
was recorded. That matches on one day of the application's life.
*Fix:* anchors containing a digit are never recorded whole, only their digit-free prefix.

**Checkpoints were literal text with no fallback.** Locators degraded through a ladder and
checkpoints did not, so rewording a page broke a run whose locators were all fine.
*Fix:* a checkpoint is recorded on the control that appeared, addressed by the same
ladder. Two of three now carry a vendor element id, which no rebrand moves.

**A control could only be anchored from above.** Anything that was last of its role before
the next piece of text had no anchor, and recorded with a single strategy.
*Fix:* anchor from below as well. One menu link went from one rung to six.

**A checkpoint preferred a piece of text over a control.** Making text readable gave text
holders an element id, and the recorder prefers a vendor id when choosing what to wait for,
because an id survives a rebrand. So text started winning that preference over real controls:
"wait for the Open New Account link" became "wait for the accounts table header", with a
90-character string among its rungs. Found by re-recording and diffing, not by reading.
*Fix:* something you could act on beats a piece of text. A heading gets reworded by a rebrand;
a form control does not. The re-record then reproduced the original artifact exactly, all
seventy-two strategies.

**A capability was named after one run's values.** `open_new_savings_account...` for a
capability whose account type is a parameter. A name is the first thing anyone believes.
*Fix:* the title, description and id are generalized before they are written.

---

## Replay

**Declared recovery had never once run.** Recovery was consulted only when a control could
not be found. Every navigation here is followed by a checkpoint, so an expired session
always arrived as "the confirmation never appeared" and was filed as a hard failure.
Found by the fault proxy, which exists because the app will not expire a session to order.
*Fix:* declared conditions are consulted on a checkpoint miss too.

**A checkpoint matched the wrong screen and the run reported success.** The loan
checkpoint's fourth rung was "the link after *Status:*". Correct on the approved screen,
and also present on the refusal screen, where it matched a navigation link. The run
returned `Home` as a loan account number.
*Fix:* a declared outcome is checked after every step, whether the checkpoint held or not.
What the application says outranks what we expected.

**A declared `hard_failure` detector was never read.** The artifact could say "if you see
this, the app has broken" and nothing consulted it.
*Fix:* failures are named from the artifact's own declarations.

**Business outcomes could only come from a dropdown.** A "record not found" page had no
path to exit 2, which is the mistake the brief calls the most common one.
*Fix:* declared business outcomes are detected from the page.

**A refusal could say that, but not why.** A `read` could only reach a form control, so
the reason a loan was refused - plain text in a table cell - had no locator at all.
*Fix:* a visible element with a stable id, holding no controls of its own, is now
readable. An outcome can say where its reason lives, and replay quotes the application
instead of paraphrasing it. This app has four refusal wordings; we report whichever came
back.

**The audit log undercounted the human.** After a handover the log recorded
`human_actions: 0` while the request sitting beside it recorded one click. The request is read
from disk before the person's actions are written into it, so the count came from a stale copy.
An audit trail that says a human did nothing, next to a file saying they did, is worse than one
that never mentions them.
*Fix:* count what was filed, not what the stale copy says.

**The evidence directory was not self-contained.** `intervention.json` is written to the
operator's inbox, which is a working directory that gets cleared and is not committed. The one
in our committed evidence was only there because somebody had copied it by hand.
*Fix:* a settled request is archived into its run's evidence directory, read back from disk so
the copy carries what the person did.

**A capability's parameters could only be learned by getting them wrong.** The model names
them, and it does not name them the same way twice, so a caller had to read the YAML or
trigger an error message.
*Fix:* `--dry-run` prints the contract, and works with no arguments at all.

---

## Safety

**Blocking a control did nothing if the page behind it was one URL away.** Told to sign
out, the model was refused the Log Out control by name, then navigated to
`/parabank/logout.htm` and signed out anyway. Found by giving a discovery run a goal the
policy forbids.
*Fix:* config denies paths as well as control names.

**Page text was never marked as untrusted.** Screen content goes into the prompt, and in a
back-office app some of it was typed by a customer.
*Fix:* the screen is fenced and the prompt says it is data, never instructions. The
allowlist remains the actual control, because a prompt is not a security boundary.

---

## Running the same goals on two models

Both capabilities were recorded twice, with Haiku 4.5 and with Sonnet 5, from identical
goals.

**A prompt written for one model produced worse artifacts on the other.** The pause before
the irreversible step said "set every value the goal specifies, even if the field already
shows it". Haiku set only what it had missed. Sonnet read it literally and re-set every
field, so its artifacts carried redundant steps: 10 against Haiku's 8 for the account, 12
against 9 for the loan. Both replayed correctly, so nothing failed and nothing would have
caught it except running both.
*Fix:* the message now separates a field left on a default from one already set. Sonnet
then produced 9, identical to Haiku.

**Parameter names are not stable, even between two runs of the same model.** The loan's
funding input was `from_account` in one Haiku run and `source_account` in the next, from
the same goal. Sonnet chose `from_account` twice, which is one data point and not a
guarantee. This is the price of letting the model design the contract.
*Fix:* the price is now paid once. `--rerecord` keeps the names, and `--dry-run` prints
them.

**What did not differ.** Both models parameterized the same slots, neither missed one or
invented one, and neither produced a stale reference, a policy denial or a failed action.
Sonnet cost roughly 1.8x Haiku per run.

---

## Process

**The agent loop had no tests.** The part the brief calls the heart of the project was
covered only by real runs against a real model, because it appeared to need both.
*Fix:* it needs neither. Thirteen tests now drive it with a scripted screen and scripted
model turns. Writing them found that discovery had been typed against the browser driver
while only ever using the six methods of the `Surface` protocol, so it now takes the
protocol. The fake screen is a second implementation of that seam, which is the clearest
evidence we have that nothing above it has quietly reached for a browser.

**Tests that could not fail.** Three tests were written for one function; two passed
against a stub that ignored its arguments. Both used a fixture that already contained the
value under test.
*Fix:* break the function on purpose and confirm the test notices. Now done for every test
that guards a specific behavior.

**Verification that skipped the important part.** Four verification passes in a row were
told to skip `discover` because it costs money. The one part never independently checked
was the one that mattered most, and the same bias showed up in the evidence folder and in
the write-up.
*Fix:* discovery is run during verification. Evidence carries seven discovery runs.
