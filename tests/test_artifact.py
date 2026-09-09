"""Schema tests.

These run with no browser and no network. The point of keeping the artifact model
pure is that its rules can be checked this cheaply.
"""

from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from finautomate.artifact import Capability, dump_capability, load_capability, referenced_params

REFERENCE = Path(__file__).parent.parent / "artifacts" / "open_new_account.yaml"


@pytest.fixture
def capability() -> Capability:
    return load_capability(REFERENCE)


# -- the reference artifact -------------------------------------------------


def test_reference_artifact_validates(capability: Capability) -> None:
    assert capability.id == "open_new_account"
    assert capability.target.surface == "browser"
    assert capability.steps[0].id == "fill_username"


def test_round_trip_is_lossless(capability: Capability, tmp_path: Path) -> None:
    out = tmp_path / "rt.yaml"
    dump_capability(capability, out)
    assert load_capability(out) == capability


def test_exactly_one_step_is_risky(capability: Capability) -> None:
    """The click that creates the account, and nothing else."""
    risky = [s.id for s in capability.steps if s.risk == "risky"]
    assert risky == ["submit_open_account"]


def test_password_is_marked_secret_and_only_passed_by_reference(
    capability: Capability,
) -> None:
    password = next(i for i in capability.inputs if i.name == "password")
    assert password.secret is True
    step = capability.step("fill_password")
    assert referenced_params(step.value) == ["password"]


def test_no_hostnames_or_css_selectors_anywhere(capability: Capability) -> None:
    """The two rules the whole design rests on, checked against the real file."""
    raw = REFERENCE.read_text()
    body = "\n".join(ln for ln in raw.splitlines() if not ln.strip().startswith("#"))
    assert "://" not in body
    for token in ("div.", "#openAccount", "input[", ".button"):
        assert token not in body, f"{token!r} looks like a CSS selector"


def test_every_select_uses_labels(capability: Capability) -> None:
    """Option values are positional codes in this app and would not survive a
    version change. See Phase 0."""
    for step in capability.steps:
        if step.action == "select":
            assert step.by == "label", step.id


# -- validators catch real authoring mistakes -------------------------------


@pytest.fixture
def raw() -> dict[str, Any]:
    parsed: dict[str, Any] = yaml.safe_load(REFERENCE.read_text())
    return parsed


def test_absolute_entry_url_is_rejected(raw: dict[str, Any]) -> None:
    raw["target"]["entry"] = "http://bank-a.example.com/parabank/index.htm"
    with pytest.raises(ValidationError, match="relative path"):
        Capability.model_validate(raw)


def test_duplicate_step_ids_rejected(raw: dict[str, Any]) -> None:
    raw["steps"][1]["id"] = raw["steps"][0]["id"]
    with pytest.raises(ValidationError, match="duplicate step ids"):
        Capability.model_validate(raw)


def test_output_reading_from_unknown_step_rejected(raw: dict[str, Any]) -> None:
    raw["outputs"][0]["from_step"] = "no_such_step"
    with pytest.raises(ValidationError, match="unknown step"):
        Capability.model_validate(raw)


def test_undeclared_outcome_rejected(raw: dict[str, Any]) -> None:
    step = next(s for s in raw["steps"] if s["id"] == "select_funding_account")
    step["outcomes"]["option_not_found"] = "NOT_A_DECLARED_OUTCOME"
    with pytest.raises(ValidationError, match="undeclared"):
        Capability.model_validate(raw)


def test_unknown_parameter_rejected(raw: dict[str, Any]) -> None:
    raw["steps"][0]["value"] = "{{not_an_input}}"
    with pytest.raises(ValidationError, match="no such input"):
        Capability.model_validate(raw)


def test_unused_input_rejected(raw: dict[str, Any]) -> None:
    raw["inputs"].append({"name": "orphan", "type": "string"})
    with pytest.raises(ValidationError, match="never used"):
        Capability.model_validate(raw)


def test_typo_in_field_name_rejected(raw: dict[str, Any]) -> None:
    """extra=forbid: a misspelled key fails loudly instead of being ignored."""
    raw["steps"][0]["risk_level"] = "safe"
    with pytest.raises(ValidationError):
        Capability.model_validate(raw)


def test_recovery_on_non_recoverable_outcome_rejected(raw: dict[str, Any]) -> None:
    outcome = next(o for o in raw["outcomes"] if o["name"] == "APP_ERROR")
    outcome["recovery"] = {"action": "restart_from", "step": "fill_username"}
    with pytest.raises(ValidationError, match="declares recovery"):
        Capability.model_validate(raw)


def test_click_without_target_rejected(raw: dict[str, Any]) -> None:
    step = next(s for s in raw["steps"] if s["id"] == "submit_login")
    del step["target"]
    with pytest.raises(ValidationError, match="needs a target"):
        Capability.model_validate(raw)


def test_enum_without_values_rejected(raw: dict[str, Any]) -> None:
    account_type = next(i for i in raw["inputs"] if i["name"] == "account_type")
    del account_type["values"]
    with pytest.raises(ValidationError, match="declares no values"):
        Capability.model_validate(raw)
