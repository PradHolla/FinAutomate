"""Command line entry point."""

import uuid
from pathlib import Path
from typing import Annotated, Any

import typer
import yaml
from anthropic import Anthropic
from playwright.sync_api import sync_playwright
from pydantic import ValidationError

from finautomate.artifact import (
    LocatorBundle,
    RoleName,
    TextVisible,
    dump_capability,
    load_capability,
)
from finautomate.checkpoint import wait_for
from finautomate.discover import DEFAULT_MODEL, MODELS, Discovery
from finautomate.evidence import Evidence
from finautomate.locate import explain, resolve
from finautomate.policy import Guards, Policy
from finautomate.replay import ParameterError, Replay, dry_run
from finautomate.result import EXIT_CODES
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
    model: Annotated[
        str, typer.Option(help=f"Which model drives discovery: {'|'.join(MODELS)}.")
    ] = DEFAULT_MODEL,
    out: Annotated[Path, typer.Option(help="Where to write the capability.")] = Path("artifacts"),
    headed: Annotated[bool, typer.Option(help="Show the browser.")] = False,
) -> None:
    """Drive a live UI with an LLM until a goal is met, then save a capability."""
    if model not in MODELS:
        raise typer.BadParameter(f"--model must be one of {sorted(MODELS)}")
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
                model=model,
            )
            capability = run.run(goal, entry, settings.get("app", "parabank"))
        finally:
            browser.close()

    rate_in, rate_out = (2, 10) if model == "sonnet" else (1, 5)
    cost = run.tokens_in / 1e6 * rate_in + run.tokens_out / 1e6 * rate_out
    typer.echo(
        f"{run.model}: {run.calls} calls, {run.tokens_in} in / {run.tokens_out} out "
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
def replay(
    artifact: Annotated[
        Path,
        typer.Argument(
            help="The capability to run.",
            # Click checks these before our code runs, so a missing file or an
            # unset shell variable gets a one-line error instead of a traceback.
            # Note Path("") is "." in Python, so an empty argument arrives as the
            # current directory rather than as nothing - hence dir_okay=False.
            exists=True,
            dir_okay=False,
            readable=True,
        ),
    ],
    param: Annotated[list[str], typer.Option(help="name=value, repeatable.")] = [],  # noqa: B006
    secret: Annotated[
        list[str], typer.Option(help="name=value for a secret input. Repeatable.")
    ] = [],  # noqa: B006
    config: Annotated[Path, typer.Option()] = Path("config/parabank.yaml"),
    attended: Annotated[
        bool, typer.Option(help="A person is watching, so risky steps may run.")
    ] = False,
    dry: Annotated[bool, typer.Option("--dry-run", help="Print the plan, touch nothing.")] = False,
    headed: Annotated[bool, typer.Option(help="Show the browser.")] = False,
) -> None:
    """Replay a saved capability with no LLM in the decision loop."""
    try:
        capability = load_capability(artifact)
    except ValidationError as err:
        typer.secho(f"{artifact} is not a valid capability:", fg=typer.colors.RED)
        for problem in err.errors():
            where = ".".join(str(p) for p in problem["loc"])
            typer.echo(f"  {where}: {problem['msg']}")
        raise typer.Exit(1) from err
    supplied = {**_pairs(param, "--param"), **_pairs(secret, "--secret")}

    if dry:
        try:
            for line in dry_run(capability, supplied):
                typer.echo(line)
        except ParameterError as err:
            typer.secho(str(err), fg=typer.colors.RED)
            raise typer.Exit(1) from err
        return

    settings = yaml.safe_load(config.read_text(encoding="utf-8"))
    run_id = f"replay-{uuid.uuid4().hex[:10]}"
    evidence = Evidence(Path("evidence"), run_id, frozenset(_pairs(secret, "--secret").values()))

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not headed)
        try:
            engine = Replay(
                capability,
                BrowserSurface(browser.new_page(), settings["base_url"]),
                evidence,
                attended=attended,
            )
            result = engine.run(supplied)
        except ParameterError as err:
            typer.secho(str(err), fg=typer.colors.RED)
            raise typer.Exit(1) from err
        finally:
            browser.close()

    _report(result)
    raise typer.Exit(EXIT_CODES[result.kind])


def _report(result: Any) -> None:
    colors = {
        "success": typer.colors.GREEN,
        "business_outcome": typer.colors.YELLOW,
        "needs_human": typer.colors.YELLOW,
        "hard_failure": typer.colors.RED,
    }
    for record in result.steps:
        flag = "  <- fallback" if record.used_fallback else ""
        tier = f"[{record.strategy_index}] {record.strategy_kind}"
        typer.echo(f"  {record.id:<32} {record.action:<8} {tier}{flag}")

    typer.secho(
        f"\n{result.kind.upper()} in {result.duration_ms}ms", fg=colors[result.kind], bold=True
    )
    if result.kind == "success":
        for name, value in result.outputs.items():
            typer.echo(f"  {name} = {value!r}")
    elif result.kind == "business_outcome":
        typer.echo(f"  {result.outcome}: {result.message}")
    elif result.kind == "needs_human":
        typer.echo(f"  held at {result.step}")
        typer.echo(f"  {result.reason}")
    elif result.kind == "hard_failure":
        typer.echo(f"  step     : {result.step}")
        typer.echo(f"  expected : {result.expected}")
        typer.echo(f"  observed : {result.observed}")
        typer.echo(f"  screenshot: {result.screenshot}")
    if drifting := result.drifting_steps:
        typer.secho(
            f"  drift warning: {drifting} needed a fallback locator", fg=typer.colors.YELLOW
        )
    typer.echo(f"  evidence : {result.evidence}")


if __name__ == "__main__":
    app()
