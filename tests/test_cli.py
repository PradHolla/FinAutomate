"""Smoke test: the CLI imports and exposes its commands.

Cheap, but it catches import errors and a broken entry point before anything else runs.
"""

from pathlib import Path

from typer.testing import CliRunner

from finautomate.artifact import Capability, load_capability
from finautomate.cli import _artifact_path, _settle_version, _write_path, app

runner = CliRunner()
REFERENCE = Path(__file__).parent / "fixtures" / "reference_capability.yaml"


def reference() -> Capability:
    return load_capability(REFERENCE)


def test_help_lists_both_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "discover" in result.output
    assert "replay" in result.output


# -- where a re-record lands, and at what version ---------------------------


def test_an_unchanged_contract_keeps_its_version_and_its_file() -> None:
    """The drift case. Re-recording to fix locators must leave callers alone."""
    prior = reference()
    settled = _settle_version(prior, prior)
    assert settled.version == prior.version
    assert _artifact_path(Path("artifacts"), settled) == Path("artifacts/open_new_account.yaml")


def test_a_changed_contract_bumps_and_lands_beside_the_old_file() -> None:
    """v1 has to survive on disk, because a caller pinned to it did not ask to move."""
    prior = reference()
    renamed = prior.model_copy(
        update={
            "inputs": [
                i.model_copy(update={"name": "source_account"})
                if i.name == "funding_account_id"
                else i
                for i in prior.inputs
            ]
        }
    )
    settled = _settle_version(prior, renamed)
    assert settled.version == prior.version + 1
    assert _artifact_path(Path("artifacts"), settled) == Path("artifacts/open_new_account.v2.yaml")


# -- you cannot silently overwrite a capability someone is calling ----------


def test_a_first_recording_takes_the_plain_name(tmp_path: Path) -> None:
    path, warning = _write_path(tmp_path, reference(), rerecording=False)
    assert path == tmp_path / "open_new_account.yaml"
    assert warning == ""


def test_an_existing_capability_is_not_overwritten(tmp_path: Path) -> None:
    """The id comes from the goal, so a second run of the same goal lands here. Its
    callers depend on names this run did not inherit."""
    (tmp_path / "open_new_account.yaml").write_text("existing", encoding="utf-8")

    path, warning = _write_path(tmp_path, reference(), rerecording=False)
    assert path == tmp_path / "open_new_account.new.yaml"
    assert "--rerecord" in warning, "the warning has to name the way out"
    assert (tmp_path / "open_new_account.yaml").read_text(encoding="utf-8") == "existing"


def test_a_rerecord_is_allowed_to_overwrite(tmp_path: Path) -> None:
    """That is the whole point of it: same contract, same file."""
    (tmp_path / "open_new_account.yaml").write_text("existing", encoding="utf-8")

    path, warning = _write_path(tmp_path, reference(), rerecording=True)
    assert path == tmp_path / "open_new_account.yaml"
    assert warning == ""
