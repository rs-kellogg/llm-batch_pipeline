from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from .attachments import attach_files_to_run
from .core import audit_run, batch_submission_summary, cancel_run, compare_runs, extrapolate_cost_from_run, find_incomplete_runs, merge_run, prepare_retry, prepare_run, status_run, submit_run, sync_checkpoint_progress, sync_run
from .scaffold import scaffold_project
from .state import load_state, resolve_run
from .validation import ProjectValidationError, validate_project


app = typer.Typer(
    name="kllm-batch",
    help="Reliable, reproducible LLM batch pipelines for research coding and extraction.",
    no_args_is_help=True,
)
console = Console()

FILE_INPUT_COST_UNKNOWN_WARNING = (
    "[bold yellow]File input cost is unknown.[/bold yellow] The prepared ${estimate:.4f} "
    "estimate covers text only; the configured budget does not cap file processing cost."
)


def _print_json(data: dict) -> None:
    console.print_json(json.dumps(data, default=str))


def _fail(exc: Exception) -> None:
    console.print(f"[bold red]Error:[/bold red] {exc}", style="red")
    raise typer.Exit(1)


def _segment_range(seg_start: int | None, seg_end: int | None) -> tuple[int, int] | None:
    if (seg_start is None) != (seg_end is None):
        raise ValueError("--seg_start and --seg_end must be provided together")
    if seg_start is None or seg_end is None:
        return None
    return seg_start, seg_end


@app.command("init")
def init_command(directory: Path = typer.Argument(..., help="Empty directory to scaffold.")):
    """Create a synthetic grant-coding project with data, codebook, prompts, and schema.

    Example: `kllm-batch init my-project`. This is local, uses no credentials,
    and makes no API calls. The destination must not already contain files.
    """
    try:
        root = scaffold_project(directory)
        console.print(f"Created project: [bold]{root}[/bold]")
        console.print(f"Next: kllm-batch validate -c {root / 'project.yaml'}")
    except Exception as exc:
        _fail(exc)


@app.command("gui")
def gui_command(
    project_directory: Path = typer.Argument(..., help="Project directory previously created with kllm-batch init."),
    port: int = typer.Option(8501, min=1, max=65535, help="Local browser port."),
    no_browser: bool = typer.Option(False, "--no-browser", help="Start the local server without opening a browser window."),
):
    """Open the local project builder; this makes no provider calls.

    First run `kllm-batch init my-project`, then `kllm-batch gui my-project`.
    The server binds only to 127.0.0.1.
    """
    if importlib.util.find_spec("streamlit") is None:
        console.print("[bold red]Streamlit is missing from this environment.[/bold red]")
        console.print(
            'Reinstall the standard package with: python -m pip install '
            '"kellogg-llm-batch @ git+https://github.com/rs-kellogg/llm-batch_pipeline.git"',
            markup=False,
        )
        raise typer.Exit(1)
    project_directory = project_directory.expanduser().resolve()
    project_file = project_directory if project_directory.name == "project.yaml" else project_directory / "project.yaml"
    if not project_file.is_file():
        console.print(f"[bold red]Initialized project not found:[/bold red] {project_file}")
        console.print(f"Create it first with: kllm-batch init {project_directory}", markup=False)
        raise typer.Exit(1)
    script = Path(__file__).with_name("gui_app.py")
    environment = os.environ.copy()
    environment["KLLM_GUI_PROJECT_DIR"] = str(project_file.parent)
    command = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(script),
        "--server.address",
        "127.0.0.1",
        "--server.port",
        str(port),
        "--server.headless",
        "true" if no_browser else "false",
        "--browser.gatherUsageStats",
        "false",
        "--server.fileWatcherType",
        "none",
        "--theme.base",
        "light",
        "--theme.baseFontSize",
        "18",
        "--theme.font",
        "sans-serif",
        "--theme.headingFont",
        "sans-serif",
        "--theme.primaryColor",
        "#4E2A84",
        "--theme.secondaryBackgroundColor",
        "#F7F5FA",
        "--theme.baseRadius",
        "medium",
        "--theme.buttonRadius",
        "medium",
    ]
    try:
        completed = subprocess.run(command, env=environment, check=False)
    except KeyboardInterrupt:
        console.print("GUI stopped.")
        return
    if completed.returncode:
        raise typer.Exit(completed.returncode)


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
    console.print(
        "[dim]Worst-case ceiling, not an expected cost: input size is approximated by "
        "character count, and every request is assumed to use the full task.max_output_tokens. "
        "Batch-rate pricing is shown; a sync/pilot run bills at the (higher) sync rate. For a "
        "realistic forecast, run a pilot and then `kllm-batch estimate-cost PILOT_RUN_DIR`.[/dim]"
    )


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
        stale_runs = find_incomplete_runs(config)
        if stale_runs:
            noun = "directory" if len(stale_runs) == 1 else "directories"
            console.print(f"[yellow]Warning:[/yellow] found {len(stale_runs)} incomplete run {noun} from an earlier `prepare` that failed or was interrupted. Nothing inside was submitted to a provider; it is safe to delete and re-run `prepare`:")
            for stale_run in stale_runs:
                console.print(f"  {stale_run}")
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
        console.print(
            "[dim]Worst-case ceiling: input size is approximated by character count, and "
            "every request assumes the full task.max_output_tokens. For a realistic forecast, "
            "run a pilot and then `kllm-batch estimate-cost PILOT_RUN_DIR`.[/dim]"
        )
        console.print(f"Inspect: {run_dir / 'REVIEW.md'}")
        console.print(f"Submit with: kllm-batch submit {run_dir}")
    except Exception as exc:
        _fail(exc)


@app.command("submit")
def submit_command(
    run: Path = typer.Argument(..., help="Run directory printed by prepare."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Confirm submission non-interactively."),
    seg_start: Optional[int] = typer.Option(
        None,
        "--seg_start",
        help="First zero-based batch segment in the range.",
    ),
    seg_end: Optional[int] = typer.Option(
        None,
        "--seg_end",
        help="Exclusive segment range end; use -1 for the final segment.",
    ),
):
    """Execute an inspected run using its recorded sync or batch mode.

    Example: `kllm-batch submit RUN_DIR --seg_start 0 --seg_end 2` submits
    segments 0 and 1. Use `--seg_end -1` to continue through the final segment.
    Omit both range options to submit every eligible segment. Requires the
    provider API key and can incur cost.
    """
    try:
        run_dir = resolve_run(run)
        manifest = json.loads((run_dir / "manifest.json").read_text())
        attachment_status = (manifest.get("file_attachments") or {}).get("status")
        if attachment_status == "required":
            raise ValueError("This retry needs files attached before submission; run 'kllm-batch attach-files'.")
        attached = attachment_status == "ready"
        estimate = manifest["cost_estimate"]["estimated_usd"]
        mode = manifest.get("execution", "batch")
        request_count = manifest["request_count"]
        segment_range = _segment_range(seg_start, seg_end)
        if mode == "sync" and segment_range is not None:
            raise ValueError(
                "Segment ranges are available only for batch runs; synchronous runs resume "
                "from per-request checkpoints"
            )
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
            if not attached:
                confirmation_estimate = estimate * confirmation_count / request_count if request_count else 0.0
            if confirmation_count == 0:
                confirmation_needed = False
                console.print(
                    f"All {request_count} synchronous requests are checkpointed. "
                    "No API requests will run; continuing local processing."
                )
        batch_summary = None
        if mode == "batch":
            batch_summary = batch_submission_summary(run_dir, segment_range)
            confirmation_count = batch_summary["request_count"]
            if confirmation_count == 0:
                cancelled = batch_summary["cancelled_segment_indexes"]
                already_submitted = batch_summary["already_submitted_indexes"]
                parts = []
                if already_submitted:
                    parts.append(f"segment(s) {already_submitted} are already submitted")
                if cancelled:
                    parts.append(f"segment(s) {cancelled} were cancelled and cannot be resubmitted")
                detail = "; ".join(parts) if parts else "no segments require submission"
                console.print(f"No action: {detail}. No API requests were run.")
                return

        noun = "request" if confirmation_count == 1 else "requests"
        qualifier = " remaining" if mode == "sync" and confirmation_count < request_count else ""
        cost_qualifier = " remaining" if qualifier else ""
        partial_batch = mode == "batch" and (
            segment_range is not None or confirmation_count < request_count
        )
        if partial_batch:
            indexes = batch_summary["pending_segment_indexes"]
            segment_noun = "segment" if len(indexes) == 1 else "segments"
            index_text = ", ".join(str(index) for index in indexes)
            range_text = ""
            if segment_range is not None:
                start, end = segment_range
                range_label = (
                    f"[{start}, -1] (-1 means through end)"
                    if end == -1
                    else f"[{start}, {end})"
                )
                selected_text = ", ".join(
                    str(index) for index in batch_summary["selected_segment_indexes"]
                )
                range_text = (
                    f"Requested range {range_label} resolves to segments "
                    f"{selected_text}. "
                )
            if attached:
                console.print(FILE_INPUT_COST_UNKNOWN_WARNING.format(estimate=estimate))
                question = (
                    f"{range_text}Submit batch {segment_noun} {index_text} containing "
                    f"{confirmation_count} {noun} with unknown total cost?"
                )
            else:
                question = (
                    f"{range_text}Submit batch {segment_noun} {index_text} containing "
                    f"{confirmation_count} {noun}? "
                    f"The full prepared run's estimated maximum cost is ${estimate:.4f}; "
                    "this subset is smaller."
                )
        elif attached:
            console.print(FILE_INPUT_COST_UNKNOWN_WARNING.format(estimate=estimate))
            question = f"Execute {confirmation_count}{qualifier} {mode} {noun} with unknown total cost?"
        else:
            question = (
                f"Execute {confirmation_count}{qualifier} {mode} {noun} with estimated maximum{cost_qualifier} "
                f"cost ${confirmation_estimate:.4f}?"
            )
        if confirmation_needed and not yes and not typer.confirm(question):
            raise typer.Abort()
        def print_sync_progress(completed: int, total: int, outcome) -> None:
            message = f"Completed {completed} out of {total} synchronous requests."
            if outcome.status != "succeeded":
                message += " [yellow]API/request failure recorded.[/yellow]"
            console.print(message)

        state = submit_run(
            run_dir,
            progress_callback=print_sync_progress if mode == "sync" else None,
            segment_range=segment_range,
        )
        action = "Processed" if mode == "sync" else "Submitted"
        console.print(f"{action} run {state['run_id']} — {state['status']}")
        if mode == "sync" and state["status"] == "completed_with_failures":
            console.print(
                "[yellow]API/request failures were recorded and were not rerun automatically.[/yellow] "
                "Inspect outputs/failures.jsonl, then use `kllm-batch retry RUN_DIR` to prepare a retry."
            )
    except typer.Abort:
        console.print("Submission cancelled.")
        raise typer.Exit()
    except typer.Exit:
        raise
    except Exception as exc:
        _fail(exc)


@app.command("attach-files")
def attach_files_command(
    run: Path = typer.Argument(..., help="Unsubmitted run directory printed by prepare."),
    column: str = typer.Option(..., help="CSV column containing one PNG or PDF filename per selected row."),
    files_dir: Path = typer.Option(..., "--files-dir", help="Directory containing the files; relative paths use the project directory."),
    acknowledge_unestimated_cost: bool = typer.Option(
        False,
        "--acknowledge-unestimated-cost",
        help="Acknowledge that the prepared cost estimate excludes image/PDF processing.",
    ),
):
    """Add one local PNG or PDF to each prepared request without provider calls."""
    try:
        attached = attach_files_to_run(
            run,
            column=column,
            files_dir=files_dir,
            acknowledge_unestimated_cost=acknowledge_unestimated_cost,
        )
        console.print(f"Attached files to run: [bold]{attached}[/bold]")
        console.print("Total cost is unknown because the original estimate covers text only.")
        console.print(f"Inspect {attached / 'REVIEW.md'} and the attached request files before submitting.")
    except Exception as exc:
        _fail(exc)


@app.command("status")
def status_command(run: Path = typer.Argument(..., help="Run directory.")):
    """Refresh remote status for a submitted or running run.

    Example: `kllm-batch status RUN_DIR`. Requires the provider API key but does
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

    Example: `kllm-batch sync RUN_DIR --watch`; polling defaults to 60 seconds.
    Requires the provider API key and accepts submitted/running runs. It is
    idempotent: rerun after an interruption to resume from saved state.
    """
    try:
        def print_watch_status(state: dict, waiting: bool) -> None:
            timestamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
            segments = ", ".join(
                f"segment {segment['index']}: {segment.get('provider_status') or segment['status']}"
                for segment in state["segments"]
            )
            next_check = f" | next check in {poll_seconds}s" if waiting else ""
            console.print(f"{timestamp} — run {state['status']} | {segments}{next_check}")

        state = sync_run(
            run,
            watch=watch,
            poll_seconds=poll_seconds,
            status_callback=print_watch_status if watch else None,
        )
        _print_state(state)
    except Exception as exc:
        _fail(exc)


@app.command("cancel")
def cancel_command(
    run: Path = typer.Argument(..., help="Run directory."),
    seg_start: Optional[int] = typer.Option(
        None,
        "--seg_start",
        help="First zero-based batch segment in the range.",
    ),
    seg_end: Optional[int] = typer.Option(
        None,
        "--seg_end",
        help="Exclusive segment range end; use -1 for the final segment.",
    ),
):
    """Cancel submitted or running remote jobs without deleting artifacts.

    Example: `kllm-batch cancel RUN_DIR --seg_start 2 --seg_end -1` cancels
    eligible remote jobs from segment 2 through the end. Omit both range
    options to cancel every eligible remote batch job. Requires the provider
    API key. Work already processed by the provider may still be billable.
    """
    try:
        segment_range = _segment_range(seg_start, seg_end)
        _print_state(
            cancel_run(
                run,
                segment_range=segment_range,
            )
        )
    except Exception as exc:
        _fail(exc)


@app.command("audit")
def audit_command(run: Path = typer.Argument(..., help="Run directory.")):
    """Audit completeness, failures, and result identifiers locally.

    Example: `kllm-batch audit RUN_DIR`. This is local and free, normally used
    after `sync`; an early audit reports missing rows without altering raw data.
    Correct provider-output problems with a linked `retry`, not manual edits.
    """
    try:
        _print_json(audit_run(run))
    except Exception as exc:
        _fail(exc)


@app.command("estimate-cost")
def estimate_cost_command(
    run: Path = typer.Argument(..., help="A synced and processed run directory (typically a pilot) with recorded usage."),
    target_rows: Optional[int] = typer.Option(
        None,
        min=1,
        help="Row count to project to; defaults to the project's full source row count.",
    ),
):
    """Project a realistic full-run cost from a completed run's actual token usage.

    Example: `kllm-batch estimate-cost PILOT_RUN_DIR`. Scales the run's real
    provider-reported token usage (run_reports/run_summary.json) by row count,
    which is far more realistic than the worst-case ceiling `prepare`/`validate`
    report. Requires the run to be synced and processed first; file-attachment
    cost is never included, regardless of the source run.
    """
    try:
        result = extrapolate_cost_from_run(run, target_rows)
        console.print(
            f"Observed {result['observed_rows']:,} row(s): "
            f"${result['observed_actual_cost_usd']:.4f} "
            f"(${result['per_row_cost_usd']:.6f}/row)."
        )
        console.print(
            f"Projected to {result['target_rows']:,} row(s): "
            f"${result['extrapolated_cost_usd']:.4f}"
        )
        if result["excludes_file_input_cost"]:
            console.print(
                "[bold yellow]Note:[/bold yellow] the source run had attached files; "
                "this projection excludes file-input cost."
            )
        _print_json(result)
    except Exception as exc:
        _fail(exc)


@app.command("retry")
def retry_command(run: Path = typer.Argument(..., help="Parent run directory.")):
    """Prepare a linked child run containing only retryable failed rows.

    Example: `kllm-batch retry RUN_DIR`. This local command requires processed
    failure output, verifies the original source checksum, recalculates cost,
    and never submits automatically. Review and submit the returned child run.
    """
    try:
        child = prepare_retry(run)
        console.print(f"Prepared retry: [bold]{child}[/bold]")
        manifest = json.loads((child / "manifest.json").read_text(encoding="utf-8"))
        if (manifest.get("file_attachments") or {}).get("status") == "required":
            console.print("Attach the same files to this retry before submitting with `kllm-batch attach-files`.")
        else:
            console.print(f"Review and submit with: kllm-batch submit {child}")
    except Exception as exc:
        _fail(exc)


@app.command("merge")
def merge_command(run: Path = typer.Argument(..., help="Latest run in an attempt chain.")):
    """Merge valid parent/retry results, preferring the newest attempt.

    Example: `kllm-batch merge CHILD_RUN_DIR`. This is local and free and needs
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

    Example: `kllm-batch compare OPENAI_RUN_DIR ANTHROPIC_RUN_DIR`. This is local
    and free. Runs are joined by record ID, never row order; missing records and
    categorical disagreements are retained in the comparison report.
    """
    try:
        _print_json(compare_runs(run_a, run_b))
    except Exception as exc:
        _fail(exc)


if __name__ == "__main__":
    app()
