# Evidence

One run per thing worth showing. Every directory here was produced by the commands
in the root `README.md`, against a live ParaBank, on 2026-09-11.

Each run writes `run.jsonl` - one JSON object per line, in order. Failures and
handovers also write a screenshot. No password appears in any of these files; the
run is given its secrets up front and redacts them from everything it writes.

## The discovery run

| | |
|---|---|
| `discovery-1b7bb3b5bf/` | Claude Haiku 4.5 driving ParaBank for the first time |

This is the run that produced `artifacts/open_new_account_funded_from_account.yaml`.
Ten model turns, eight recorded steps. `run.jsonl` shows each action the model chose,
the policy decision on it, and what was recorded. `transcript.json` is the raw
conversation, kept separate because the artifact is meant to stand on its own.

Worth looking at: `precondition_held`, where the model tried to open the account
before setting every parameter and was refused.

## Replay runs

All six replay the same artifact with no model involved.

| Directory | Outcome | Exit | What it shows |
|---|---|---|---|
| `replay-b2672a57ad/` | success | 0 | the ordinary path. Every step resolves at strategy 0 |
| `replay-fb945c7cff/` | business outcome | 2 | asked for funding account 99999. The application answered, and the answer was no. Not an error |
| `replay-eb783f4e7b/` | needs human | 3 | unattended, so it stopped before the irreversible step and raised an intervention |
| `replay-bf4c8565e9/` | hard failure | 1 | the call that opens the account was broken by the fault proxy. Reported as `APP_ERROR`, with a screenshot |
| `replay-1fd1e665ef/` | success after recovery | 0 | the fault proxy dropped the session mid-flow. See the `recovering` event, then the flow restarting from the top |
| `replay-3a70c80f84/` | success at a second tenant | 0 | the same artifact against a rebranded ParaBank. All eight steps fell to a lower-tier locator and it still worked |

The last three use `finautomate proxy` to inject the failure, because a healthy
ParaBank will not expire a session or break on request. See "Breaking it on purpose"
in the root README.

## The human handover

| | |
|---|---|
| `replay-0d5fb2c4f4/` | a person took over the live session and finished the step by hand |

The one run here a person drove. The automation stopped at the irreversible step,
wrote an intervention request, and waited. A person clicked the button themselves in
the same browser window, then ran `resolve <id> --handled`. Control came back and the
run finished.

`intervention.json` carries the whole record: which capability, which step, why it
stopped, what was on screen, who decided, and what they did while they held it -
`click` on `input "Open New Account"`, captured by the page itself.

**This one is older than the rest.** It was recorded before the capability was
re-recorded, so it names the earlier capability id. The handover mechanism has not
changed; re-running it needs a person at a keyboard, and the record of what happened
is worth more than a matching id.
