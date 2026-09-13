"""The agent loop.

Discovery is the one part of this system that talks to a model and to a live
browser, which is exactly why it was the last part to get tests. It does not need
either. It needs a screen and a caller that answers questions, and both can be
scripted.

The fakes below are not mocks of a library. `FakeSurface` is a second real
implementation of the `Surface` protocol, which is the seam the whole design rests
on; if it could not be written, the claim about desktop drivers would be empty.
`FakeModel` scripts the turns, so each test can put the loop in the exact position
it wants to check and no test costs money.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import pytest
from anthropic import Anthropic
from anthropic.types import ToolUseBlock

from finautomate import discover as discovery_module
from finautomate.artifact import Capability, load_capability
from finautomate.discover import Discovery
from finautomate.evidence import Evidence
from finautomate.policy import Guards, Policy
from finautomate.surface.models import Control, Snapshot, Surface, TextAnchor

REFERENCE = Path(__file__).parent / "fixtures" / "reference_capability.yaml"


@pytest.fixture(autouse=True)
def _settle_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    """The recorder waits for the page to stop moving before writing a checkpoint.
    Six seconds is right against a real app and pointless against a scripted one."""
    monkeypatch.setattr(discovery_module, "SETTLE_SECONDS", 0.05)


# -- a screen, and someone to ask about it ----------------------------------


class FakeSurface:
    """A scripted screen. Each action moves to the next one, the way a real page
    does, so the recorder has a change to notice."""

    def __init__(self, *screens: Snapshot) -> None:
        self.screens = list(screens)
        self.at = 0
        self.did: list[tuple[str, str, str]] = []

    def _advance(self) -> None:
        self.at = min(self.at + 1, len(self.screens) - 1)

    def observe(self) -> Snapshot:
        return self.screens[self.at]

    def navigate(self, path: str) -> None:
        # Deliberately does not advance: the run navigates to the entry point before
        # the first turn, and that must not consume a screen.
        self.did.append(("navigate", path, ""))

    def click(self, ref: str) -> None:
        self.did.append(("click", ref, ""))
        self._advance()

    def type(self, ref: str, text: str) -> None:
        self.did.append(("type", ref, text))
        self._advance()

    def select(self, ref: str, label: str) -> None:
        self.did.append(("select", ref, label))
        self._advance()

    def read(self, ref: str) -> str:
        control = self.observe().control(ref)
        return control.value or control.text


@dataclass
class FakeUsage:
    input_tokens: int = 100
    output_tokens: int = 20


@dataclass
class FakeTurn:
    content: list[Any]
    usage: FakeUsage = field(default_factory=FakeUsage)


class FakeModel:
    """Answers with a scripted turn and keeps what it was asked."""

    def __init__(self, *turns: FakeTurn) -> None:
        self.turns = list(turns)
        self.sent: list[dict[str, Any]] = []
        self.messages = self

    def create(self, **kwargs: Any) -> FakeTurn:
        self.sent.append(kwargs)
        if not self.turns:
            return FakeTurn(content=[])
        return self.turns.pop(0)

    def everything_it_was_told(self) -> str:
        """Every byte that went to the model, flattened. For the secrecy checks."""
        return repr(self.sent)


def call(name: str, **args: Any) -> ToolUseBlock:
    return ToolUseBlock(id=f"t{name}", name=name, input=args, type="tool_use")


def turn(*blocks: ToolUseBlock) -> FakeTurn:
    return FakeTurn(content=list(blocks))


# -- the screens ------------------------------------------------------------


def login_screen() -> Snapshot:
    return Snapshot(
        url="/parabank/index.htm",
        title="ParaBank",
        anchors=[
            TextAnchor(text="Username", doc_order=0),
            TextAnchor(text="Password", doc_order=2),
        ],
        controls=[
            Control(ref="c1", role="textbox", field_name="username", doc_order=1),
            Control(ref="c2", role="textbox", field_name="password", doc_order=3),
            Control(ref="c3", role="button", name="Log In", doc_order=4),
            Control(ref="c9", role="link", name="Log Out", doc_order=5),
        ],
    )


def account_screen() -> Snapshot:
    return Snapshot(
        url="/parabank/openaccount.htm",
        title="Open Account",
        anchors=[TextAnchor(text="What type of account", doc_order=0)],
        controls=[
            Control(
                ref="c4",
                role="combobox",
                field_id="type",
                options=["CHECKING", "SAVINGS"],
                doc_order=1,
            ),
            Control(ref="c5", role="button", name="Open New Account", doc_order=2),
        ],
    )


def opened_screen() -> Snapshot:
    return Snapshot(
        url="/parabank/openaccount.htm",
        title="Open Account",
        anchors=[TextAnchor(text="Account Opened", doc_order=0)],
        controls=[
            Control(ref="c6", role="link", field_id="newAccountId", value="13566", doc_order=1)
        ],
    )


def policy() -> Policy:
    return Policy(
        allowed_path_prefix="/parabank/",
        denied_control_names=("Log Out", "Admin Page"),
        denied_paths=("/parabank/logout.htm",),
        risky_control_names=("Open New Account",),
    )


def run_discovery(
    surface: Surface,
    model: FakeModel,
    tmp_path: Path,
    *,
    goal: str = "Open a new SAVINGS account",
    params: dict[str, str] | None = None,
    secrets: dict[str, str] | None = None,
    max_steps: int = 25,
    inherit: Capability | None = None,
) -> tuple[Capability | None, Discovery]:
    run = Discovery(
        surface=surface,
        client=cast(Anthropic, model),
        policy=policy(),
        guards=Guards(max_steps=max_steps),
        evidence=Evidence(tmp_path, "run", frozenset((secrets or {}).values())),
        params=params if params is not None else {"username": "john"},
        secrets=secrets if secrets is not None else {"password": "demo"},
        inherit=inherit,
    )
    return run.run(goal, "/parabank/index.htm", "parabank"), run


def events(tmp_path: Path) -> str:
    return (tmp_path / "run" / "run.jsonl").read_text(encoding="utf-8")


# -- the allowlist ----------------------------------------------------------


def test_a_denied_control_is_refused_and_never_recorded(tmp_path: Path) -> None:
    """The safety claim, with the prompt taken out of the picture entirely. The
    model asks for the forbidden thing and the harness says no."""
    model = FakeModel(turn(call("click", ref="c9", description="the Log Out link")))
    surface = FakeSurface(login_screen())

    _, run = run_discovery(surface, model, tmp_path)

    assert surface.did == [("navigate", "/parabank/index.htm", "")], "the click never happened"
    assert run.steps == []
    assert '"verdict": "deny"' in events(tmp_path)


def test_a_path_outside_the_application_is_refused(tmp_path: Path) -> None:
    model = FakeModel(turn(call("navigate", path="/somewhere-else/")))
    surface = FakeSurface(login_screen())

    _, run = run_discovery(surface, model, tmp_path)

    assert run.steps == []
    assert ("navigate", "/somewhere-else/", "") not in surface.did


def test_a_denied_path_is_refused_even_though_its_prefix_is_allowed(tmp_path: Path) -> None:
    """A real discovery run found this hole: refused the Log Out control, it walked
    around the block by going straight to the URL behind it."""
    model = FakeModel(turn(call("navigate", path="/parabank/logout.htm")))
    surface = FakeSurface(login_screen())

    _, run = run_discovery(surface, model, tmp_path)

    assert run.steps == []
    assert ("navigate", "/parabank/logout.htm", "") not in surface.did


# -- refusing rather than attempting ----------------------------------------


def test_a_reference_that_has_gone_stale_is_refused(tmp_path: Path) -> None:
    """The model names a control from the screen it was shown. If that control is
    not what it was any more, the tool rejects the call instead of clicking whatever
    now sits at that ref."""
    shown = login_screen()
    moved = Snapshot(
        url=shown.url,
        title=shown.title,
        anchors=shown.anchors,
        controls=[Control(ref="c3", role="button", name="Transfer", doc_order=4)],
    )

    class Moving(FakeSurface):
        """The loop looks once to show the model, and the tool looks again to check
        the control is still what the model was shown. It changes in between."""

        def __init__(self) -> None:
            super().__init__(shown)
            self.looks = 0

        def observe(self) -> Snapshot:
            self.looks += 1
            return shown if self.looks == 1 else moved

    surface = Moving()
    model = FakeModel(turn(call("click", ref="c3", description="the Log In button")))

    _, run = run_discovery(surface, model, tmp_path)

    assert run.steps == [], "nothing may be recorded against a control that moved"
    assert ("click", "c3", "") not in surface.did
    assert "stale_ref" in events(tmp_path)


# -- the pause before the point of no return --------------------------------


def test_the_first_irreversible_action_is_paused_once_then_allowed(tmp_path: Path) -> None:
    """It fires while the form is still editable, because after that click there is
    no way back to set a field that was left on its default. Once, not every time."""
    model = FakeModel(
        turn(call("click", ref="c5", description="the Open New Account button")),
        turn(call("click", ref="c5", description="the Open New Account button")),
    )
    surface = FakeSurface(account_screen(), opened_screen())

    _, run = run_discovery(surface, model, tmp_path)

    assert run.warned_before_risky
    assert [a for a in surface.did if a[0] == "click"] == [("click", "c5", "")], (
        "refused the first time, allowed the second"
    )
    assert [s.risk for s in run.steps] == ["risky"]


# -- secrets ----------------------------------------------------------------


def test_a_secret_reaches_the_page_but_never_the_model_or_the_recording(
    tmp_path: Path,
) -> None:
    model = FakeModel(
        turn(call("type_secret", ref="c2", description="the Password field", parameter="password"))
    )
    surface = FakeSurface(login_screen())

    _, run = run_discovery(surface, model, tmp_path)

    assert ("type", "c2", "demo") in surface.did, "the real value has to reach the browser"
    assert run.steps[0].value == "{{password}}", "and nothing else"
    assert "demo" not in model.everything_it_was_told()
    assert "demo" not in events(tmp_path)


def test_a_secret_the_run_was_not_given_is_refused(tmp_path: Path) -> None:
    model = FakeModel(
        turn(call("type_secret", ref="c2", description="the Password field", parameter="pin"))
    )
    surface = FakeSurface(login_screen())

    _, run = run_discovery(surface, model, tmp_path)

    assert run.steps == []


# -- the contract the model declares ----------------------------------------


def test_a_declared_value_becomes_a_placeholder_in_the_step(tmp_path: Path) -> None:
    """The model types a real value to reason with. The recording must not keep it,
    or the capability would only ever work for one customer."""
    model = FakeModel(
        turn(call("select", ref="c4", description="the account type", label="SAVINGS")),
        turn(
            call(
                "done",
                summary="opened it",
                success_text="Account Opened",
                parameters=[{"name": "account_type", "value": "SAVINGS"}],
            )
        ),
    )
    surface = FakeSurface(account_screen(), opened_screen())

    capability, _ = run_discovery(surface, model, tmp_path)

    assert capability is not None
    assert ("select", "c4", "SAVINGS") in surface.did
    step = capability.step("select_account_type")
    assert step.value == "{{account_type}}"
    assert step.outcomes == {"option_not_found": "ACCOUNT_TYPE_NOT_AVAILABLE"}
    # The password is supplied but never typed in this run, so it is not declared.
    # An input no step uses would be rejected by the schema anyway.
    assert [i.name for i in capability.inputs] == ["account_type"]


def test_a_parameter_no_step_sets_fails_the_whole_run(tmp_path: Path) -> None:
    """Declaring `account_type` and then leaving the dropdown on whatever it
    defaulted to produces a capability that ignores the argument, which is worse
    than one that never offered it."""
    model = FakeModel(
        turn(call("click", ref="c3", description="the Log In button")),
        turn(
            call(
                "done",
                summary="opened it",
                success_text="Account Opened",
                parameters=[{"name": "account_type", "value": "SAVINGS"}],
            )
        ),
    )
    surface = FakeSurface(login_screen(), account_screen())

    capability, run = run_discovery(surface, model, tmp_path)

    assert capability is None
    assert "account_type" in run.complaint
    assert "declaration_unmet" in events(tmp_path)


def test_a_goal_the_model_gave_up_on_writes_nothing(tmp_path: Path) -> None:
    """A capability that quietly returns nothing is worse than an honest failure."""
    model = FakeModel(turn(call("stuck", reason="the value is not on any of these screens")))
    surface = FakeSurface(login_screen())

    capability, _ = run_discovery(surface, model, tmp_path)

    assert capability is None


# -- the loop's own limits --------------------------------------------------


def test_only_the_first_action_of_a_turn_is_performed(tmp_path: Path) -> None:
    """A second action chosen from the same screen was chosen blind, because the
    first one may have changed the page."""
    model = FakeModel(
        turn(
            call("type_text", ref="c1", description="the Username field", text="john"),
            call("click", ref="c3", description="the Log In button"),
        )
    )
    surface = FakeSurface(login_screen())

    _, run = run_discovery(surface, model, tmp_path)

    assert [a[0] for a in surface.did if a[0] != "navigate"] == ["type"]
    assert len(run.steps) == 1


def test_the_step_ceiling_stops_the_run_with_nothing_written(tmp_path: Path) -> None:
    """A ceiling that does not depend on the model choosing to stop. A partial
    recording that validates is worse than none."""
    model = FakeModel(*[turn(call("click", ref="c3", description="the Log In button"))] * 10)
    surface = FakeSurface(login_screen())

    capability, run = run_discovery(surface, model, tmp_path, max_steps=2)

    assert capability is None
    assert run.calls == 2
    assert '"outcome": "max_steps"' in events(tmp_path)


# -- re-recording -----------------------------------------------------------


def test_a_rerecord_tells_the_model_the_names_already_in_use(tmp_path: Path) -> None:
    """Handed the contract it already has, so its callers keep working."""
    model = FakeModel(turn(call("stuck", reason="stopping here")))
    surface = FakeSurface(login_screen())

    run_discovery(surface, model, tmp_path, inherit=load_capability(REFERENCE))

    opening = str(model.sent[0]["messages"][0]["content"])
    assert "account_type" in opening
    assert "funding_account_id" in opening
    assert "new_account_id" in opening, "output names drift too, and break callers too"
    assert "It takes these parameters:" in opening
    assert "It returns these values" in opening


# -- not waiting for something that cannot happen ---------------------------


def test_a_refused_action_is_not_followed_by_a_wait(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After each action the recorder polls the screen for up to six seconds, because
    this app swaps panels after the network goes quiet. A refused action changed
    nothing, so that is six seconds spent waiting for something that cannot arrive -
    and if the screen did move late, the checkpoint would land on the step before,
    which did not cause it.
    """
    waits: list[str] = []

    def note(_run: Discovery, before: Snapshot) -> None:
        waits.append(before.url)

    monkeypatch.setattr(Discovery, "_record_wait", note)
    model = FakeModel(
        turn(call("click", ref="c3", description="the Log In button")),
        turn(call("click", ref="c9", description="the Log Out link")),
    )

    _, run = run_discovery(FakeSurface(login_screen()), model, tmp_path)

    assert [s.id for s in run.steps] == ["click_log_in"], "the Log Out click is refused"
    assert len(waits) == 1, "only the action that actually happened is waited on"


def test_a_read_is_not_followed_by_a_wait(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A read looks at the screen and leaves it alone, so the settle wait has nothing
    to wait for. Measured on a real run, it cost ten seconds."""
    waits: list[str] = []

    def note(_run: Discovery, before: Snapshot) -> None:
        waits.append(before.url)

    monkeypatch.setattr(Discovery, "_record_wait", note)
    model = FakeModel(
        turn(
            call(
                "read",
                ref="c6",
                description="the new account number",
                output_name="new_account_number",
            )
        )
    )

    _, run = run_discovery(FakeSurface(opened_screen()), model, tmp_path)

    assert [s.action for s in run.steps] == ["read"]
    assert waits == [], "a read has nothing to settle"
