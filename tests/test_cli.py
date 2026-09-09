"""Smoke test: the CLI imports and exposes its commands.

Cheap, but it catches import errors and a broken entry point before anything else runs.
"""

from typer.testing import CliRunner

from finautomate.cli import app

runner = CliRunner()


def test_help_lists_both_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "discover" in result.output
    assert "replay" in result.output
