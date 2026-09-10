"""Command line entry point."""

import uuid
from pathlib import Path
from typing import Annotated

import typer
import yaml
from anthropic import Anthropic
from playwright.sync_api import sync_playwright

from finautomate.artifact import LocatorBundle, RoleName, TextVisible, dump_capability
from finautomate.checkpoint import wait_for
from finautomate.discover import Discovery
from finautomate.evidence import Evidence
from finautomate.locate import explain, resolve
from finautomate.policy import Guards, Policy
from finautomate.surface.browser import BrowserSurface

app = typer.Typer(
    add_completion=False,
    help="Record a UI flow with an LLM once, then replay it deterministically.",
)


def _pairs(values: list[str], flag: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for value in values:
        name, _, literal = value.partition("=")
        if not name or not _:
            raise typer.BadParameter(f"{flag} expects name=value, got {value!r}")
        out[name] = literal
    return out


@app.command()
def discover(
    goal: Annotated[str, typer.Argument(help="What to accomplish, in plain English.")],
    config: Annotated[Path, typer.Option(help="Target and policy config.")] = Path(
        "config/parabank.yaml"
    ),
    entry: Annotated[str, typer.Option(help="Path to start from.")] = "/parabank/index.htm",
    param: Annotated[list[str], typer.Option(help="name=value, repeatable.")] = [],  # noqa: B006
    secret: Annotated[
        list[str], typer.Option(help="name=value the model never sees. Repeatable.")
    ] = [],  # noqa: B006
    expect_output: Annotated[
        list[str], typer.Option(help="A value the capability must return. Repeatable.")
    ] = [],  # noqa: B006
    out: Annotated[Path, typer.Option(help="Where to write the capability.")] = Path("artifacts"),
    headed: Annotated[bool, typer.Option(help="Show the browser.")] = False,
) -> None:
    """Drive a live UI with an LLM until a goal is met, then save a capability."""
    settings = yaml.safe_load(config.read_text(encoding="utf-8"))
    policy = Policy.model_validate(settings["policy"])
    guards = Guards.model_validate(settings.get("guards", {}))
    params, secrets = _pairs(param, "--param"), _pairs(secret, "--secret")

    run_id = f"discovery-{uuid.uuid4().hex[:10]}"
    evidence = Evidence(Path("evidence"), run_id, frozenset(secrets.values()))
    typer.echo(f"run {run_id}  goal: {goal}")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not headed)
        try:
            run = Discovery(
                surface=BrowserSurface(browser.new_page(), settings["base_url"]),
                client=Anthropic(),
                policy=policy,
                guards=guards,
                evidence=evidence,
                params=params,
                secrets=secrets,
                expect_outputs=tuple(expect_output),
            )
            capability = run.run(goal, entry, settings.get("app", "parabank"))
        finally:
            browser.close()

    cost = run.tokens_in / 1e6 * 2 + run.tokens_out / 1e6 * 10
    typer.echo(
        f"{run.calls} model calls, {run.tokens_in} in / {run.tokens_out} out "
        f"tokens, about ${cost:.3f}"
    )
    typer.echo(f"evidence: {evidence.dir}")

    if capability is None:
        typer.secho("no capability recorded - see the evidence log", fg=typer.colors.RED)
        raise typer.Exit(1)

    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{capability.id}.yaml"
    dump_capability(capability, path)
    typer.secho(f"recorded {len(capability.steps)} steps to {path}", fg=typer.colors.GREEN)


@app.command()
def reset(
    config: Annotated[Path, typer.Option(help="Target config.")] = Path("config/parabank.yaml"),
    clean: Annotated[
        bool, typer.Option(help="Strip the demo data instead of restoring it.")
    ] = False,
) -> None:
    """Restore the target application's demo data to a known state.

    Replays create real records, so runs need a clean starting point. This drives
    the app's own admin screen through the same surface driver and locator
    resolution the replay engine uses - no CSS selectors, no special casing.
    """
    settings = yaml.safe_load(config.read_text(encoding="utf-8"))
    options = settings["reset"]
    label = options["clean"] if clean else options["initialize"]

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            surface = BrowserSurface(browser.new_page(), settings["base_url"])
            surface.navigate(options["path"])
            target = LocatorBundle(
                description=f"the {label} button",
                strategies=[RoleName(kind="role_name", role="button", name=label)],
            )
            found = resolve(target, surface.observe())
            if found.control is None:
                typer.secho(explain(target, found), fg=typer.colors.RED)
                raise typer.Exit(1)
            surface.click(found.control.ref)
            confirmed = TextVisible(
                kind="text_visible",
                text=options["confirms"],
                match="contains",
                timeout_ms=20_000,
            )
            settled = wait_for(surface, confirmed)
        finally:
            browser.close()

    if settled is None:
        typer.secho(f"{label} did not confirm within the timeout", fg=typer.colors.RED)
        raise typer.Exit(1)
    outcome = "stripped of demo data" if clean else "restored to its demo data"
    typer.secho(f"target {outcome}", fg=typer.colors.GREEN)


@app.command()
def replay() -> None:
    """Replay a saved capability with no LLM in the decision loop."""
    raise NotImplementedError("Phase 4")


if __name__ == "__main__":
    app()
