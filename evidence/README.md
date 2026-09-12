# Evidence

One run per thing worth showing. Every directory was produced by the commands in the
root `README.md`, against a live ParaBank.

Each run writes `run.jsonl` - one JSON object per line, in order. Failures and
handovers also write a screenshot. No password appears in any of these files: a run is
given its secrets up front and redacts them from everything it writes.

## Discovery runs

Two capabilities, each recorded by Claude Haiku 4.5 driving the real UI once.

| Run | Produced | Model turns |
|---|---|---|
| `discovery-0ee77cc1c6/` | `artifacts/open_new_account_funded_from_account.yaml`, 8 steps | 10 |
| `discovery-f33009db49/` | `artifacts/apply_for_loan_with_down_payment.yaml`, 9 steps | 11 |

Neither artifact was written by hand. Each names its run in a `recorded:` block, so you
can go from any capability back to the log of the run that produced it.

`run.jsonl` shows each action the model chose, the policy decision on it, and what was
recorded. `transcript.json` is the raw conversation, kept separate because the artifact
is meant to stand on its own.

Worth looking at in the first one: `precondition_held`, where the model tried to open
the account before setting every parameter and was refused.

## Replay runs

No model in any of these.

| Directory | Outcome | Exit | What it shows |
|---|---|---|---|
| `replay-ae52f6ddd3/` | success | 0 | open an account. Every step resolves at strategy 0 |
| `replay-d171432721/` | success | 0 | apply for a loan, approved. A second capability on the same engine |
| `replay-ffdd522ed5/` | business outcome | 2 | **the bank refused the loan.** The application considered the request and said no |
| `replay-86bc73e9f2/` | business outcome | 2 | a funding account the customer does not own. The other kind of "no" - the caller got it wrong |
| `replay-ebd6add42c/` | needs human | 3 | unattended, so it stopped before the irreversible step |
| `replay-0dc6c4b8b6/` | hard failure | 1 | the account-opening call broken by the fault proxy. Reported as `APP_ERROR`, with a screenshot |
| `replay-c7e26cfb69/` | success after recovery | 0 | the proxy dropped the session mid-flow. See the `recovering` event, then the run restarting from the top |
| `replay-9ba9c82d18/` | success at a second tenant | 0 | the same artifact against a rebranded ParaBank. All eight steps fell to a lower-tier locator and it still worked |

The two exit-2 runs are the point of the error taxonomy and they are not the same
thing. One is the caller asking for an account that is not theirs. The other is the
bank weighing an application and declining it. Both are answers; neither is a crash.

The last three use `finautomate proxy` to inject the failure, because a healthy
ParaBank will not expire a session or break on request. See "Breaking it on purpose"
in the root README.

## The human handover

| | |
|---|---|
| `replay-0d5fb2c4f4/` | a person took over the live session and finished the step by hand |

The automation stopped at the irreversible step, wrote an intervention request, and
waited. A person clicked the button themselves in the same browser window, then ran
`resolve <id> --handled`. Control came back and the run finished.

`intervention.json` carries the whole record: which capability, which step, why it
stopped, what was on screen, who decided, and what they did while they held it -
`click` on `input "Open New Account"`, captured by the page itself.

**This one is older than the rest.** It was recorded before the capability was
re-recorded, so it names an earlier capability id. The handover mechanism has not
changed; re-running it needs a person at a keyboard, and the record of what actually
happened is worth more than a matching id.
