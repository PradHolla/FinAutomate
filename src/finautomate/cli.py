"""Command line entry point."""

import threading
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
    Outcome,
    RoleName,
    TextVisible,
    dump_capability,
    load_capability,
)
from finautomate.checkpoint import wait_for
from finautomate.discover import DEFAULT_MODEL, MODELS, Discovery
from finautomate.evidence import Evidence
from finautomate.faultproxy import load_faults, serve
from finautomate.locate import explain
from finautomate.locate import resolve as resolve_locator
from finautomate.policy import Guards, Policy
from finautomate.replay import ParameterError, Replay, dry_run, with_runtime_outcomes
from finautomate.result import EXIT_CODES
from finautomate.session import InterventionStore, Status
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
                model=model,
            )
            capability = run.run(goal, entry, settings.get("app", "parabank"))
        finally:
            browser.close()

    # Anthropic bills a cache write at 1.25x the input rate and a cache read at 0.1x.
    rate_in, rate_out = (2, 10) if model == "sonnet" else (1, 5)
    cost = (
        run.tokens_in * rate_in
        + run.cache_written * rate_in * 1.25
        + run.cache_read * rate_in * 0.10
        + run.tokens_out * rate_out
    ) / 1e6
    typer.echo(
        f"{run.model}: {run.calls} calls, {run.tokens_in} in / {run.tokens_out} out, "
        f"cache {run.cache_written} written / {run.cache_read} read, about ${cost:.3f}"
    )
    typer.echo(f"evidence: {evidence.dir}")

    if capability is None:
        why = run.complaint or "see the evidence log"
        typer.secho(f"no capability recorded: {why}", fg=typer.colors.RED)
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
            found = resolve_locator(target, surface.observe())
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
    base_url: Annotated[
        str,
        typer.Option(
            help="Override the config's base URL. Used to route a run through the "
            "fault proxy without inventing a tenant that does not exist.",
        ),
    ] = "",
    attended: Annotated[
        bool, typer.Option(help="A person is watching, so risky steps may run.")
    ] = False,
    wait_for_human: Annotated[
        int,
        typer.Option(
            help="Seconds to hold the session open for an operator at a risky step. "
            "0 means raise the request and exit.",
        ),
    ] = 0,
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
    # The application's own runtime conditions - a session that expires, an error
    # page - come from tenant config, because no recording will ever contain them.
    capability = with_runtime_outcomes(
        capability, [Outcome.model_validate(o) for o in settings.get("outcomes", [])]
    )
    run_id = f"replay-{uuid.uuid4().hex[:10]}"
    evidence = Evidence(Path("evidence"), run_id, frozenset(_pairs(secret, "--secret").values()))

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not headed)
        try:
            engine = Replay(
                capability,
                BrowserSurface(browser.new_page(), base_url or settings["base_url"]),
                evidence,
                attended=attended,
                interventions=InterventionStore() if wait_for_human else None,
                wait_seconds=wait_for_human,
                announce=typer.echo,
            )
            result = engine.run(supplied)
        except ParameterError as err:
            typer.secho(str(err), fg=typer.colors.RED)
            raise typer.Exit(1) from err
        finally:
            browser.close()

    _report(result)
    raise typer.Exit(EXIT_CODES[result.kind])


@app.command()
def proxy(
    rules: Annotated[
        Path,
        typer.Option(help="Which faults to inject.", exists=True, dir_okay=False, readable=True),
    ],
    port: Annotated[int, typer.Option(help="Port to listen on.")] = 8888,
) -> None:
    """Run the fault-injection proxy in front of the target application.

    A test instrument. Point a tenant config's `base_url` at this port and the
    system under test does not know anything changed - which is the point, because
    a driver that can lie to itself is not worth testing.
    """
    faults = load_faults(rules)
    server = serve(faults, port, typer.echo)
    typer.secho(f"proxying http://localhost:{port} -> {faults.upstream}", fg=typer.colors.GREEN)
    for rule in faults.rules:
        typer.echo(f"  {'once ' if rule.once else 'every'}  {rule.name}")
    typer.echo("ctrl-c to stop")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        typer.echo("\nstopped")
    finally:
        server.shutdown()


@app.command()
def interventions() -> None:
    """Show requests waiting for a person."""
    pending = InterventionStore().pending()
    if not pending:
        typer.echo("nothing waiting")
        return
    for request in pending:
        typer.secho(f"\n{request.id}", bold=True)
        typer.echo(f"  capability : {request.capability}")
        typer.echo(f"  held at    : {request.step}")
        typer.echo(f"  why        : {request.reason}")
        typer.echo(f"  screenshot : {request.screenshot}")
        typer.echo(f"  controller : {request.controller}")
    typer.echo("\nresolve with: uv run finautomate resolve <id> --approve | --handled | --reject")


@app.command()
def resolve(
    intervention_id: Annotated[str, typer.Argument(help="Which request.")],
    approve: Annotated[
        bool, typer.Option("--approve", help="Let the automation perform the step.")
    ] = False,
    handled: Annotated[
        bool, typer.Option("--handled", help="You did it yourself; skip the step and continue.")
    ] = False,
    reject: Annotated[bool, typer.Option("--reject", help="Do not proceed.")] = False,
    operator: Annotated[str, typer.Option(help="Who decided.")] = "operator",
    note: Annotated[str, typer.Option(help="Why.")] = "",
) -> None:
    """Hand control back to the automation with a decision.

    `--approve` and `--handled` are deliberately different. One is the machine
    acting with permission; the other is a person acting instead of the machine.
    An audit of a bank's systems cares which.
    """
    picked: list[tuple[Status, bool]] = [
        ("approved", approve),
        ("handled", handled),
        ("rejected", reject),
    ]
    chosen = [name for name, on in picked if on]
    if len(chosen) != 1:
        raise typer.BadParameter("choose exactly one of --approve, --handled, --reject")
    try:
        updated = InterventionStore().resolve(
            intervention_id,
            chosen[0],
            operator=operator,
            note=note or None,
        )
    except (FileNotFoundError, ValueError) as err:
        typer.secho(str(err), fg=typer.colors.RED)
        raise typer.Exit(1) from err
    typer.secho(
        f"{updated.id}: {updated.status}, control returned to the agent", fg=typer.colors.GREEN
    )


COLORS = {
    "success": typer.colors.GREEN,
    "business_outcome": typer.colors.YELLOW,
    "needs_human": typer.colors.YELLOW,
    "hard_failure": typer.colors.RED,
}


def _detail(result: Any) -> list[str]:
    """The lines specific to one kind of outcome."""
    if result.kind == "success":
        return [f"  {name} = {value!r}" for name, value in result.outputs.items()]
    if result.kind == "business_outcome":
        return [f"  {result.outcome}: {result.message}"]
    if result.kind == "hard_failure":
        return [
            f"  step      : {result.step}",
            f"  expected  : {result.expected}",
            f"  observed  : {result.observed}",
            f"  screenshot: {result.screenshot}",
        ]
    lines = [f"  held at {result.step}", f"  {result.reason}"]
    if not result.intervention:
        lines.append(
            "\n  Nobody was asked, because this run was not waiting for anyone."
            "\n  To hand the live session to a person instead, add:"
            "\n      --wait-for-human 300 --headed"
        )
    return lines


def _report(result: Any) -> None:
    for record in result.steps:
        flag = "  <- fallback" if record.used_fallback else ""
        tier = f"[{record.strategy_index}] {record.strategy_kind}"
        typer.echo(f"  {record.id:<32} {record.action:<8} {tier}{flag}")

    typer.secho(
        f"\n{result.kind.upper()} in {result.duration_ms}ms", fg=COLORS[result.kind], bold=True
    )
    for line in _detail(result):
        typer.echo(line)

    if drifting := result.drifting_steps:
        # A count, not the list: every step above already carries its own marker.
        typer.secho(
            f"  drift warning: {len(drifting)} of {len(result.steps)} steps needed a "
            "fallback locator",
            fg=typer.colors.YELLOW,
        )
    typer.echo(f"  evidence : {result.evidence}")


if __name__ == "__main__":
    app()
