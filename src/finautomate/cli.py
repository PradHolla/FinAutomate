"""Command line entry point."""

import typer

app = typer.Typer(
    add_completion=False,
    help="Record a UI flow with an LLM once, then replay it deterministically.",
)


@app.command()
def discover() -> None:
    """Drive a live UI with an LLM until a goal is met, then save a capability."""
    raise NotImplementedError("Phase 3")


@app.command()
def replay() -> None:
    """Replay a saved capability with no LLM in the decision loop."""
    raise NotImplementedError("Phase 4")


if __name__ == "__main__":
    app()
