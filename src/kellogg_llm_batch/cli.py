from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from .core import audit_run, cancel_run, compare_runs, generate_pilot, merge_run, prepare_retry, prepare_run, resolve_pilot, run_pilot, status_run, submit_run, sync_run
from .scaffold import scaffold_project
from .state import resolve_run
from .validation import ProjectValidationError, validate_project


app = typer.Typer(
    name="kllm-batch",
    help="Reliable, reproducible LLM batch pipelines for research coding and extraction.",
    no_args_is_help=True,
)
pilot_app = typer.Typer(
    help="Generate inspectable pilot artifacts, then run those exact saved requests.",
    no_args_is_help=True,
)
app.add_typer(pilot_app, name="pilot")
console = Console()


def _print_json(data: dict) -> None:
    console.print_json(json.dumps(data, default=str))


def _fail(exc: Exception) -> None:
    console.print(f"[bold red]Error:[/bold red] {exc}", style="red")
    raise typer.Exit(1)


@app.command("init")
def init_command(directory: Path = typer.Argument(..., help="Empty directory to scaffold.")):
    """Create an annotated project, prompts, schema, and sample data.

    Example: `kllm-batch init my-project`. This is local, uses no credentials,
    and makes no API calls. The destination must not already contain files.
    """
    try:
        root = scaffold_project(directory)
        console.print(f"Created project: [bold]{root}[/bold]")
        console.print(f"Next: kllm-batch validate -c {root / 'project.yaml'}")
    except Exception as exc:
        _fail(exc)


@app.command("validate")
def validate_command(
    config: Path = typer.Option(..., "--config", "-c", help="Path to project.yaml."),
    report: Optional[Path] = typer.Option(None, help="Optional path for a JSON validation report."),
):
    """Validate data, duplicates, prompts, schema, sizes, and estimated cost.

    Example: `kllm-batch validate -c project.yaml --report validation.json`.
    This is local, free, and valid in any workflow state. Fix every ERROR
    upstream before preparing; no run artifacts are created on failure.
    """
    try:
        result = validate_project(config, report)
    except ProjectValidationError as exc:
        _print_validation(exc.report)
        raise typer.Exit(1)
    except Exception as exc:
        _fail(exc)
    _print_validation(result)


def _print_validation(report: dict) -> None:
    color = "green" if report["valid"] else "red"
    console.print(f"[{color}]Validation {'passed' if report['valid'] else 'failed'}[/{color}]")
    console.print(f"Rows: {report['source_rows']:,} | Requests: {report['request_count']:,}")
    for finding in report["findings"]:
        console.print(f"  {finding['severity'].upper()} {finding['code']}: {finding['message']}")
    for provider, estimate in report["cost_estimates"].items():
        value = "unknown" if estimate["estimated_usd"] is None else f"${estimate['estimated_usd']:.4f}"
        console.print(f"  {provider}: estimated maximum {value}")


@app.command("prepare")
def prepare_command(
    config: Path = typer.Option(..., "--config", "-c", help="Path to project.yaml."),
    provider: str = typer.Option(..., help="Configured provider: openai or anthropic."),
):
    """Create an immutable, costed run after repeating local validation.

    Example: `kllm-batch prepare -c project.yaml --provider openai`. No API
    key or remote call is required. Preparation stops on validation, pricing,
    or budget errors; correct the project and prepare a new run.
    """
    try:
        run_dir = prepare_run(config, provider)
        manifest = json.loads((run_dir / "manifest.json").read_text())
        console.print(f"Prepared run: [bold]{run_dir}[/bold]")
        console.print(f"Estimated maximum cost: ${manifest['cost_estimate']['estimated_usd']:.4f}")
        console.print(f"Submit with: kllm-batch submit {run_dir}")
    except Exception as exc:
        _fail(exc)


@app.command("submit")
def submit_command(
    run: Path = typer.Argument(..., help="Run directory printed by prepare."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Confirm submission non-interactively."),
):
    """Submit a prepared run after confirming its recorded estimate.

    Example: `kllm-batch submit RUN_ID`; use `--yes` only in reviewed
    automation. Requires the provider API key and can incur cost. Repeating the
    command is idempotent for segments whose remote batch IDs were saved.
    """
    try:
        run_dir = resolve_run(run)
        manifest = json.loads((run_dir / "manifest.json").read_text())
        estimate = manifest["cost_estimate"]["estimated_usd"]
        if not yes and not typer.confirm(f"Submit {manifest['request_count']} requests with estimated maximum cost ${estimate:.4f}?"):
            raise typer.Abort()
        state = submit_run(run_dir)
        console.print(f"Submitted run {state['run_id']}")
    except typer.Abort:
        console.print("Submission cancelled.")
        raise typer.Exit()
    except Exception as exc:
        _fail(exc)


@app.command("status")
def status_command(run: Path = typer.Argument(..., help="Run directory.")):
    """Refresh remote status for a submitted or running run.

    Example: `kllm-batch status RUN_ID`. Requires the provider API key but does
    not submit new work. If a prior command was interrupted, inspect the local
    state and rerun this command before `sync`.
    """
    try:
        state = status_run(run)
        _print_state(state)
    except Exception as exc:
        _fail(exc)


def _print_state(state: dict) -> None:
    table = Table(title=f"Run {state['run_id']} — {state['status']}")
    table.add_column("Segment")
    table.add_column("Local status")
    table.add_column("Provider status")
    table.add_column("Batch ID")
    for segment in state["segments"]:
        table.add_row(str(segment["index"]), segment["status"], str(segment.get("provider_status") or ""), str(segment.get("remote_batch_id") or ""))
    console.print(table)


@app.command("sync")
def sync_command(
    run: Path = typer.Argument(..., help="Run directory."),
    watch: bool = typer.Option(False, help="Poll until all provider jobs reach a terminal state."),
    poll_seconds: int = typer.Option(60, min=1, help="Seconds between polls when --watch is used."),
):
    """Download, normalize, validate, and audit completed segments.

    Example: `kllm-batch sync RUN_ID --watch`; polling defaults to 60 seconds.
    Requires the provider API key and accepts submitted/running runs. It is
    idempotent: rerun after an interruption to resume from saved state.
    """
    try:
        state = sync_run(run, watch=watch, poll_seconds=poll_seconds)
        _print_state(state)
    except Exception as exc:
        _fail(exc)


@app.command("cancel")
def cancel_command(run: Path = typer.Argument(..., help="Run directory.")):
    """Cancel submitted or running remote jobs without deleting artifacts.

    Example: `kllm-batch cancel RUN_ID`. Requires the provider API key. Work
    already processed by the provider may still be billable; run `sync` later
    if partial results become available.
    """
    try:
        _print_state(cancel_run(run))
    except Exception as exc:
        _fail(exc)


@app.command("audit")
def audit_command(run: Path = typer.Argument(..., help="Run directory.")):
    """Audit completeness, failures, and result identifiers locally.

    Example: `kllm-batch audit RUN_ID`. This is local and free, normally used
    after `sync`; an early audit reports missing rows without altering raw data.
    Correct provider-output problems with a linked `retry`, not manual edits.
    """
    try:
        _print_json(audit_run(run))
    except Exception as exc:
        _fail(exc)


@app.command("retry")
def retry_command(run: Path = typer.Argument(..., help="Parent run directory.")):
    """Prepare a linked child run containing only retryable failed rows.

    Example: `kllm-batch retry RUN_ID`. This local command requires processed
    failure output, verifies the original source checksum, recalculates cost,
    and never submits automatically. Review and submit the returned child run.
    """
    try:
        child = prepare_retry(run)
        console.print(f"Prepared retry: [bold]{child}[/bold]")
        console.print(f"Review and submit with: kllm-batch submit {child}")
    except Exception as exc:
        _fail(exc)


@app.command("merge")
def merge_command(run: Path = typer.Argument(..., help="Latest run in an attempt chain.")):
    """Merge valid parent/retry results, preferring the newest attempt.

    Example: `kllm-batch merge CHILD_RUN_ID`. This is local and free and needs
    processed result files. Missing rows remain missing and are visible in the
    audit; source or raw result files are never modified.
    """
    try:
        console.print(f"Merged results: [bold]{merge_run(run)}[/bold]")
    except Exception as exc:
        _fail(exc)


@app.command("compare")
def compare_command(
    run_a: Path = typer.Argument(..., help="First processed run."),
    run_b: Path = typer.Argument(..., help="Second processed run."),
):
    """Compare two processed runs and export rows needing human review.

    Example: `kllm-batch compare OPENAI_RUN_ID ANTHROPIC_RUN_ID`. This is local
    and free. Runs are joined by record ID, never row order; missing records and
    categorical disagreements are retained in the comparison report.
    """
    try:
        _print_json(compare_runs(run_a, run_b))
    except Exception as exc:
        _fail(exc)


@pilot_app.command("generate")
def pilot_generate_command(
    config: Path = typer.Option(..., "--config", "-c", help="Path to project.yaml."),
    provider: str = typer.Option(..., help="Configured provider: openai or anthropic."),
):
    """Generate a deterministic sample and rendered requests for inspection.

    Example: `kllm-batch pilot generate -c project.yaml --provider openai`.
    This is local and free. Validation runs first; the sample size and seed come
    from project.yaml. Inspect sampled_records.jsonl, rendered_prompts.jsonl,
    provider_requests.jsonl, schema.json, and manifest.json before running.
    """
    try:
        pilot_dir = generate_pilot(config, provider)
        manifest = json.loads((pilot_dir / "manifest.json").read_text(encoding="utf-8"))
        console.print(f"Generated pilot: [bold]{pilot_dir}[/bold]")
        console.print(f"Sampled records: {manifest['sample_size']} | Requests: {manifest['request_count']}")
        console.print(f"Inspect: {pilot_dir / 'rendered_prompts.jsonl'}")
        console.print(f"Run with: kllm-batch pilot run {pilot_dir}")
    except Exception as exc:
        _fail(exc)


@pilot_app.command("run")
def pilot_run_command(
    pilot: Path = typer.Argument(..., help="Pilot directory printed by 'pilot generate'."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Confirm the API-backed pilot non-interactively."),
    rerun: bool = typer.Option(False, help="Intentionally execute an already-run pilot again; may incur duplicate cost."),
):
    """Execute the exact provider requests saved by `pilot generate`.

    Example: `kllm-batch pilot run PILOT_DIR`. This requires the selected
    provider's API key and incurs cost. Review the generated artifacts first.
    A completed pilot cannot run again unless --rerun is explicitly supplied.
    """
    try:
        pilot_dir = resolve_pilot(pilot)
        manifest = json.loads((pilot_dir / "manifest.json").read_text(encoding="utf-8"))
        estimate = manifest["pilot_cost_estimate"].get("estimated_usd")
        cost_text = "unknown" if estimate is None else f"${estimate:.4f}"
        console.print(f"Pilot: {manifest['sample_size']} records in {manifest['request_count']} request(s); estimated maximum {cost_text}.")
        if not yes and not typer.confirm("Run these exact saved API requests?"):
            raise typer.Abort()
        _print_json(run_pilot(pilot_dir, rerun=rerun))
    except typer.Abort:
        console.print("Pilot cancelled.")
        raise typer.Exit()
    except Exception as exc:
        _fail(exc)


if __name__ == "__main__":
    app()
