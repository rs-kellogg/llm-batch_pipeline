from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from .core import audit_run, cancel_run, compare_runs, merge_run, prepare_retry, prepare_run, status_run, submit_run, sync_checkpoint_progress, sync_run
from .scaffold import scaffold_project
from .state import load_state, resolve_run
from .validation import ProjectValidationError, validate_project


app = typer.Typer(
    name="kllm-batch",
    help="Reliable, reproducible LLM batch pipelines for research coding and extraction.",
    no_args_is_help=True,
)
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
    sample_size: Optional[int] = typer.Option(None, min=1, help="Prepare a deterministic random sample instead of all rows."),
    seed: Optional[int] = typer.Option(None, help="Random seed used with --sample-size; defaults to evaluation.random_seed in project.yaml."),
    ids_file: Optional[Path] = typer.Option(None, help="UTF-8 text file containing one source record ID per line, with no header."),
    execution: Optional[str] = typer.Option(None, help="Execution mode: sync or batch. Defaults to sync for a selection and batch for all rows."),
):
    """Select records and create an immutable, inspectable, costed run.

    Full run: `kllm-batch prepare -c project.yaml --provider openai`.
    Pilot: add `--sample-size 20 --seed 42`; or use `--ids-file IDs.txt`.
    Preparation is local and free. Start with REVIEW.md, then inspect the exact
    provider JSONL—including its rendered prompts—before submitting.
    """
    try:
        run_dir = prepare_run(
            config,
            provider,
            sample_size=sample_size,
            seed=seed,
            ids_file=ids_file,
            execution=execution,
        )
        manifest = json.loads((run_dir / "manifest.json").read_text())
        console.print(f"Prepared run: [bold]{run_dir}[/bold]")
        console.print(f"Purpose: {manifest['purpose']} | Selection: {manifest['selection']['method']} | Execution: {manifest['execution']}")
        console.print(f"Selected rows: {manifest['selected_rows']:,} of {manifest['source_total_rows']:,}")
        console.print(f"Estimated maximum cost: ${manifest['cost_estimate']['estimated_usd']:.4f}")
        console.print(f"Inspect: {run_dir / 'REVIEW.md'}")
        console.print(f"Submit with: kllm-batch submit {run_dir}")
    except Exception as exc:
        _fail(exc)


@app.command("submit")
def submit_command(
    run: Path = typer.Argument(..., help="Run directory printed by prepare."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Confirm submission non-interactively."),
):
    """Execute an inspected run using its recorded sync or batch mode.

    Example: `kllm-batch submit RUN_ID`; use `--yes` only in reviewed
    automation. Requires the provider API key and can incur cost. Synchronous
    runs process immediately; batch runs continue through status and sync.
    """
    try:
        run_dir = resolve_run(run)
        manifest = json.loads((run_dir / "manifest.json").read_text())
        estimate = manifest["cost_estimate"]["estimated_usd"]
        mode = manifest.get("execution", "batch")
        request_count = manifest["request_count"]
        confirmation_count = request_count
        confirmation_estimate = estimate
        confirmation_needed = True
        if mode == "sync":
            existing_state = load_state(run_dir)
            progress = sync_checkpoint_progress(run_dir)
            if existing_state.get("status") in {"completed", "completed_with_failures"}:
                if progress["missing_ids"] or progress["unexpected_ids"]:
                    console.print(
                        f"[bold yellow]Integrity warning:[/bold yellow] state.json marks "
                        f"{progress['total']} of {progress['total']} requests completed, but only "
                        f"{progress['checkpointed']} of {progress['total']} expected responses exist "
                        "in raw_responses."
                    )
                    if progress["missing_ids"]:
                        console.print(f"Missing request IDs: {', '.join(progress['missing_ids'][:10])}")
                    if progress["unexpected_ids"]:
                        console.print(f"Unexpected response IDs: {', '.join(progress['unexpected_ids'][:10])}")
                    console.print(
                        "No API requests were run. Restore the immutable raw response, or use the source "
                        "record IDs from input_snapshot/request_map.jsonl to prepare a new run with "
                        "`kllm-batch prepare --ids-file rerun_ids.txt`."
                    )
                    raise typer.Exit(1)
                total = progress["total"]
                console.print(
                    f"No action: {total} of {total} synchronous requests already finished "
                    f"(status: {existing_state['status']}). No API requests were run and no new run was created."
                )
                if existing_state["status"] == "completed_with_failures":
                    console.print(
                        "[yellow]Recorded failures remain in outputs/failures.jsonl; use "
                        "`kllm-batch retry .` to prepare a retry.[/yellow]"
                    )
                return
            if progress["unexpected_ids"]:
                console.print(
                    f"[bold yellow]Integrity warning:[/bold yellow] raw_responses contains unexpected "
                    f"request IDs: {', '.join(progress['unexpected_ids'][:10])}. No API requests were run."
                )
                raise typer.Exit(1)
            if progress["checkpointed"]:
                console.print(
                    f"Resuming partial synchronous run: {progress['checkpointed']} of {progress['total']} "
                    f"requests are checkpointed and will be skipped; {progress['remaining']} "
                    f"{'remains' if progress['remaining'] == 1 else 'remain'}."
                )
                if progress["remaining"]:
                    console.print(
                        "[yellow]Warning:[/yellow] an API response received immediately before an interruption "
                        "may lack a checkpoint, so one unrecorded request could run again and incur duplicate cost."
                    )
                if progress["recorded_failures"]:
                    console.print(
                        f"[yellow]{progress['recorded_failures']} checkpointed API/request failure(s) will "
                        "not be rerun automatically; use `kllm-batch retry` after processing.[/yellow]"
                    )
            confirmation_count = progress["remaining"]
            confirmation_estimate = estimate * confirmation_count / request_count if request_count else 0.0
            if confirmation_count == 0:
                confirmation_needed = False
                console.print(
                    f"All {request_count} synchronous requests are checkpointed. "
                    "No API requests will run; continuing local processing."
                )
        noun = "request" if confirmation_count == 1 else "requests"
        qualifier = " remaining" if mode == "sync" and confirmation_count < request_count else ""
        cost_qualifier = " remaining" if qualifier else ""
        if confirmation_needed and not yes and not typer.confirm(
            f"Execute {confirmation_count}{qualifier} {mode} {noun} with estimated maximum{cost_qualifier} "
            f"cost ${confirmation_estimate:.4f}?"
        ):
            raise typer.Abort()
        def print_sync_progress(completed: int, total: int, outcome) -> None:
            message = f"Completed {completed} out of {total} synchronous requests."
            if outcome.status != "succeeded":
                message += " [yellow]API/request failure recorded.[/yellow]"
            console.print(message)

        state = submit_run(run_dir, progress_callback=print_sync_progress if mode == "sync" else None)
        action = "Processed" if mode == "sync" else "Submitted"
        console.print(f"{action} run {state['run_id']} — {state['status']}")
        if mode == "sync" and state["status"] == "completed_with_failures":
            console.print(
                "[yellow]API/request failures were recorded and were not rerun automatically.[/yellow] "
                "Inspect outputs/failures.jsonl, then use `kllm-batch retry RUN_ID` to prepare a retry."
            )
    except typer.Abort:
        console.print("Submission cancelled.")
        raise typer.Exit()
    except typer.Exit:
        raise
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


if __name__ == "__main__":
    app()
