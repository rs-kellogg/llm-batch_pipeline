from __future__ import annotations

import json
import math
import os
import platform
import shutil
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import pandas as pd
from jsonschema import Draft202012Validator

from ._version import __version__
from .config import ProjectConfig, load_config
from .data import canonicalize, load_source, normalize_id
from .models import CanonicalRequest, NormalizedResult
from .pricing import estimate_cost
from .prompts import load_context, load_prompt_files, render_user_prompt
from .providers.base import ProviderAdapter, get_provider
from .schema import load_row_schema, wrapped_schema
from .state import load_state, resolve_run, save_state
from .utils import RunLock, atomic_write_json, json_default, sha256_file, sha256_text, utc_now
from .validation import validate_project


def _json_line(obj: dict[str, Any]) -> bytes:
    return (json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def _manifest(run_dir: Path) -> dict[str, Any]:
    return json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))


def _canonical_input_path(run_dir: Path) -> Path:
    """Locate canonical input in current runs and runs made before v0.1."""
    current = run_dir / "internal" / "canonical_input.parquet"
    legacy = run_dir / "requests" / "canonical_input.parquet"
    return current if current.exists() else legacy


def _build_requests(config: ProjectConfig, provider_name: str, selected_ids: set[str] | None = None):
    df, source, _ = load_source(config)
    records = canonicalize(config, df, source)
    if selected_ids is not None:
        records = [record for record in records if record.record_id in selected_ids]
        missing = selected_ids - {record.record_id for record in records}
        if missing:
            raise ValueError(f"Selected IDs do not exist in source: {sorted(missing)[:20]}")
    system_prompt, user_template = load_prompt_files(config)
    context = load_context(config)
    schema = wrapped_schema(load_row_schema(config))
    settings = config.providers[provider_name]
    adapter = get_provider(provider_name)
    canonical: list[CanonicalRequest] = []
    payloads: list[dict[str, Any]] = []
    for index, start in enumerate(range(0, len(records), config.task.rows_per_request)):
        group = records[start : start + config.task.rows_per_request]
        custom_id = f"request_{index:08d}"
        prompt_records = [{"record_id": record.record_id, **record.sent} for record in group]
        user_prompt = render_user_prompt(user_template, prompt_records, context)
        canonical.append(CanonicalRequest(custom_id=custom_id, record_ids=[r.record_id for r in group], system_prompt=system_prompt, user_prompt=user_prompt))
        payloads.append(adapter.build_payload(custom_id, settings.model, system_prompt, user_prompt, schema, config.task.max_output_tokens, settings.options))
    return records, canonical, payloads, source, schema


def _split_payloads(payloads: list[dict[str, Any]], max_requests: int, max_bytes: int) -> list[list[dict[str, Any]]]:
    segments: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_bytes = 0
    for payload in payloads:
        size = len(_json_line(payload))
        if size > max_bytes:
            raise ValueError(f"One request is {size:,} bytes, above the configured {max_bytes:,}-byte batch limit")
        if current and (len(current) >= max_requests or current_bytes + size > max_bytes):
            segments.append(current)
            current = []
            current_bytes = 0
        current.append(payload)
        current_bytes += size
    if current:
        segments.append(current)
    return segments


def _select_ids(
    config: ProjectConfig,
    *,
    selected_ids: set[str] | None,
    sample_size: int | None,
    seed: int | None,
    ids_file: str | Path | None,
    parent_run: str | None,
) -> tuple[set[str] | None, dict[str, Any], str]:
    choices = sum(value is not None for value in (selected_ids, sample_size, ids_file))
    if choices > 1:
        raise ValueError("Use only one record selection method: sample_size or ids_file")
    if seed is not None and sample_size is None:
        raise ValueError("seed can only be used with sample_size")
    if selected_ids is not None:
        return selected_ids, {"method": "retry", "selected_count": len(selected_ids)}, "retry"
    if ids_file is not None:
        path = Path(ids_file).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(f"ID selection file not found: {path}")
        values = [normalize_id(line) for line in path.read_text(encoding="utf-8").splitlines()]
        ids = [value for value in values if value]
        duplicates = sorted(record_id for record_id, count in Counter(ids).items() if count > 1)
        if not ids:
            raise ValueError(f"ID selection file contains no nonempty IDs: {path}")
        if duplicates:
            raise ValueError(f"ID selection file contains duplicate IDs: {duplicates[:20]}")
        return set(ids), {"method": "ids_file", "selected_count": len(ids), "ids_file": str(path), "ids_file_sha256": sha256_file(path)}, "pilot"
    if sample_size is not None:
        if sample_size <= 0:
            raise ValueError("sample_size must be greater than zero")
        source_frame, source, _ = load_source(config)
        records = canonicalize(config, source_frame, source)
        if sample_size > len(records):
            raise ValueError(f"sample_size={sample_size} exceeds the {len(records)} available records")
        effective_seed = config.evaluation.random_seed if seed is None else seed
        sampled = pd.Series([record.record_id for record in records]).sample(n=sample_size, random_state=effective_seed).tolist()
        return set(sampled), {"method": "random", "selected_count": sample_size, "seed": effective_seed}, "pilot"
    purpose = "retry" if parent_run else "production"
    return None, {"method": "all"}, purpose


def prepare_run(
    config_or_path: ProjectConfig | str | Path,
    provider: str,
    *,
    sample_size: int | None = None,
    seed: int | None = None,
    ids_file: str | Path | None = None,
    execution: str | None = None,
    selected_ids: set[str] | None = None,
    parent_run: str | None = None,
) -> Path:
    config = config_or_path if isinstance(config_or_path, ProjectConfig) else load_config(config_or_path)
    if provider not in config.providers:
        raise ValueError(f"Provider '{provider}' is not configured")
    validation = validate_project(config)
    selected_ids, selection, purpose = _select_ids(
        config,
        selected_ids=selected_ids,
        sample_size=sample_size,
        seed=seed,
        ids_file=ids_file,
        parent_run=parent_run,
    )
    execution = execution or ("sync" if purpose == "pilot" else "batch")
    if execution not in {"batch", "sync"}:
        raise ValueError("execution must be 'batch' or 'sync'")
    records, canonical, payloads, source, schema = _build_requests(config, provider, selected_ids)
    if not records:
        raise ValueError("No records selected for preparation")
    adapter = get_provider(provider)
    settings = config.providers[provider]
    max_requests = settings.max_requests_per_batch or adapter.default_max_requests
    max_bytes = settings.max_batch_bytes or adapter.default_max_bytes
    segments = _split_payloads(payloads, max_requests, max_bytes)
    estimate = estimate_cost(config, provider, [request.system_prompt + "\n" + request.user_prompt for request in canonical], execution)
    if estimate.estimated_usd is None:
        raise ValueError(f"No pricing is configured for {provider}/{settings.model}; add provider price overrides")
    if estimate.estimated_usd > config.budget.max_estimated_usd:
        raise ValueError(f"Estimated cost ${estimate.estimated_usd:.4f} exceeds budget ${config.budget.max_estimated_usd:.4f}")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = f"{timestamp}_{provider}_{uuid.uuid4().hex[:8]}"
    runs_root = config.resolve(config.output.runs_directory)
    final_run_dir = runs_root / run_id
    run_dir = runs_root / f".{run_id}.building"
    for name in ("requests", "internal", "snapshot"):
        (run_dir / name).mkdir(parents=True, exist_ok=False if name == "requests" else True)

    canonical_rows = []
    for record in records:
        source_row_sha256 = sha256_text(
            json.dumps(
                {"record_id": record.record_id, "sent": record.sent, "preserved": record.preserved},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        canonical_rows.append({"record_id": record.record_id, "source_row": record.source_row, **record.sent, **record.preserved, "_kllm_source_row_sha256": source_row_sha256, "_kllm_truncated_fields": json.dumps(record.truncated_fields, sort_keys=True)})
    canonical_df = pd.DataFrame(canonical_rows)
    canonical_df.to_parquet(run_dir / "internal" / "canonical_input.parquet", index=False)

    gold_labels_path = None
    if config.evaluation.gold_columns:
        source_frame, _, _ = load_source(config)
        gold_rows = []
        for record in records:
            row = {"record_id": record.record_id}
            row.update({output_field: source_frame.iloc[record.source_row][source_column] for output_field, source_column in config.evaluation.gold_columns.items()})
            gold_rows.append(row)
        gold_labels_path = run_dir / "internal" / "gold_labels.parquet"
        pd.DataFrame(gold_rows).to_parquet(gold_labels_path, index=False)

    canonical_by_id = {item.custom_id: item for item in canonical}
    state_segments = []
    mapping_rows: list[dict[str, Any]] = []
    for index, segment in enumerate(segments):
        request_path = run_dir / "requests" / f"segment_{index:04d}.jsonl"
        request_path.write_bytes(b"".join(_json_line(item) for item in segment))
        for item in segment:
            request = canonical_by_id[item["custom_id"]]
            mapping_rows.append({"segment_index": index, "custom_id": request.custom_id, "record_ids": request.record_ids})
        state_segments.append({"index": index, "request_file": str(request_path.relative_to(run_dir)), "mapping_file": "internal/request_map.jsonl", "status": "prepared", "remote_batch_id": None, "input_file_id": None, "provider_status": None, "error": None})
    with (run_dir / "internal" / "request_map.jsonl").open("w", encoding="utf-8") as handle:
        for item in mapping_rows:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")

    system_prompt, user_template = load_prompt_files(config)
    shutil.copy2(config.resolve(config.prompt.system_file), run_dir / "snapshot" / "system.txt")
    shutil.copy2(config.resolve(config.prompt.user_file), run_dir / "snapshot" / "user.txt")
    shutil.copy2(config.resolve(config.task.output_schema), run_dir / "snapshot" / "schema.json")
    shutil.copy2(config.config_path, run_dir / "snapshot" / "project.yaml")
    context_manifest = {}
    if config.prompt.context:
        (run_dir / "snapshot" / "context").mkdir()
    for name, item in config.prompt.context.items():
        source_context = config.resolve(item.path)
        destination = run_dir / "snapshot" / "context" / f"{name}{source_context.suffix}"
        shutil.copy2(source_context, destination)
        context_manifest[name] = {"source_path": str(source_context), "snapshot_path": str(destination.relative_to(run_dir)), "sha256": sha256_file(source_context)}
    prepared_artifacts = {
        str(path.relative_to(run_dir)): sha256_file(path)
        for directory in (run_dir / "requests", run_dir / "internal", run_dir / "snapshot")
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }
    manifest = {
        "run_id": run_id,
        "created_at": utc_now(),
        "project": config.project.name,
        "provider": provider,
        "model_requested": settings.model,
        "config_path": str(config.config_path),
        "source_path": str(source),
        "source_sha256": sha256_file(source),
        "config_sha256": sha256_file(config.config_path),
        "source_rows": len(canonical_df),
        "source_total_rows": validation["source_rows"],
        "selected_rows": len(canonical_df),
        "request_count": len(payloads),
        "segment_count": len(segments),
        "prompt_version": config.prompt.version,
        "system_prompt_sha256": sha256_text(system_prompt),
        "user_prompt_sha256": sha256_text(user_template),
        "schema_sha256": sha256_text(json.dumps(schema, sort_keys=True)),
        "package_version": __version__,
        "python_version": platform.python_version(),
        "package_versions": _package_versions(),
        "git_commit": _git_commit(config.base_dir),
        "provider_options": settings.options,
        "purpose": purpose,
        "execution": execution,
        "selection": selection,
        "gold_columns": config.evaluation.gold_columns,
        "gold_labels_file": str(gold_labels_path.relative_to(run_dir)) if gold_labels_path else None,
        "parent_run": parent_run,
        "cost_estimate": estimate.model_dump(),
        "output_options": config.output.model_dump(mode="json"),
        "context": context_manifest,
        "prepared_artifact_sha256": prepared_artifacts,
    }
    atomic_write_json(run_dir / "manifest.json", manifest)
    _write_review(run_dir, manifest, records)
    save_state(run_dir, {"run_id": run_id, "stage": "prepared", "status": "prepared", "created_at": manifest["created_at"], "segments": state_segments})
    os.replace(run_dir, final_run_dir)
    return final_run_dir


def _git_commit(path: Path) -> str | None:
    import subprocess
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=path, text=True, capture_output=True)
    return result.stdout.strip() if result.returncode == 0 else None


def _package_versions() -> dict[str, str | None]:
    versions = {}
    for package in ("kellogg-llm-batch", "openai", "anthropic", "pandas", "pyarrow", "pydantic", "pyyaml", "jsonschema", "typer"):
        try:
            versions[package] = version(package)
        except PackageNotFoundError:
            versions[package] = None
    return versions


def _write_review(run_dir: Path, manifest: dict[str, Any], records: list[Any]) -> None:
    estimate = manifest["cost_estimate"]["estimated_usd"]
    selection = manifest["selection"]
    if manifest["provider"] == "openai":
        prompt_locations = "`body.instructions` (system prompt) and `body.input` (user prompt)"
        inspection_command = "jq -r '.body.instructions, .body.input' requests/segment_*.jsonl"
    else:
        prompt_locations = "`params.system` (system prompt) and `params.messages[].content` (user prompt)"
        inspection_command = "jq -r '.params.system, .params.messages[].content' requests/segment_*.jsonl"
    lines = [
        "# Review before submission",
        "",
        f"- Project: {manifest['project']}",
        f"- Purpose: {manifest['purpose']}",
        f"- Selection: {selection['method']}",
        f"- Records: {manifest['selected_rows']:,} of {manifest['source_total_rows']:,}",
        f"- Requests: {manifest['request_count']:,} across {manifest['segment_count']:,} segment(s)",
        f"- Provider/model: {manifest['provider']} / {manifest['model_requested']}",
        f"- Execution: {manifest['execution']}",
        f"- Estimated maximum cost: ${estimate:.4f}",
        "",
        "## What to inspect",
        "",
        f"1. `requests/segment_*.jsonl` contains the exact provider-native payloads that will be executed. The prompts are at {prompt_locations}.",
        "2. `manifest.json` records selection, hashes, model, pricing, and environment provenance.",
        "",
        "To print every rendered system and user prompt (requires `jq`):",
        "",
        "```bash",
        "# Run from inside this run directory",
        inspection_command,
        "```",
        "",
        "Files under `internal/` and `snapshot/` support joins, retries, validation, and reproducibility; they normally do not need manual review.",
    ]
    if manifest["purpose"] == "pilot":
        record_ids = [record.record_id for record in records]
        preview = record_ids[:20]
        lines.extend(["", "## Selected record IDs", "", *[f"- `{record_id}`" for record_id in preview]])
        if len(record_ids) > len(preview):
            lines.append(f"- …and {len(record_ids) - len(preview):,} more; see `internal/canonical_input.parquet`.")
    lines.extend(["", "## Submit after review", "", "From inside this run directory:", "", "```bash", "kllm-batch submit .", "```", ""])
    (run_dir / "REVIEW.md").write_text("\n".join(lines), encoding="utf-8")


def _verify_prepared_artifacts(run_dir: Path, manifest: dict[str, Any]) -> None:
    artifacts = manifest.get("prepared_artifact_sha256") or manifest.get("request_artifact_sha256", {})
    for relative_path, expected_hash in artifacts.items():
        path = run_dir / relative_path
        if not path.exists() or sha256_file(path) != expected_hash:
            raise ValueError(
                f"Prepared artifact changed after review: {path}. "
                "Update the project inputs and run 'prepare' again."
            )


def _execute_sync_requests(run_dir: Path, manifest: dict[str, Any], adapter: ProviderAdapter) -> dict:
    (run_dir / "raw").mkdir(exist_ok=True)
    with RunLock(run_dir):
        state = load_state(run_dir)
        if state.get("status") in {"completed", "completed_with_failures"}:
            return state
        for segment in state["segments"]:
            if segment["status"] in {"downloaded", "processed"}:
                continue
            output_path = run_dir / "raw" / f"segment_{segment['index']:04d}_output.jsonl"
            completed_ids: set[str] = set()
            if output_path.exists():
                for line in output_path.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        item = json.loads(line)
                        if "_kllm_normalized" in item:
                            completed_ids.add(str(item["_kllm_normalized"]["custom_id"]))
            with output_path.open("a", encoding="utf-8") as handle:
                for payload in ProviderAdapter.read_jsonl(run_dir / segment["request_file"]):
                    custom_id = str(payload["custom_id"])
                    if custom_id in completed_ids:
                        continue
                    try:
                        outcome = adapter.run_sync(payload)
                    except Exception as exc:
                        outcome = NormalizedResult(
                            custom_id=custom_id,
                            status="errored",
                            error_type="sync_request_error",
                            error_message=str(exc),
                        )
                    handle.write(json.dumps({"_kllm_normalized": outcome.model_dump()}, ensure_ascii=False) + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
            segment.update(status="downloaded", provider_status="completed", error=None)
            state["stage"] = "downloaded"
            save_state(run_dir, state)
    _process_downloads(run_dir, adapter)
    return load_state(run_dir)


def submit_run(run: str | Path, adapter: ProviderAdapter | None = None) -> dict:
    run_dir = resolve_run(run)
    manifest = _manifest(run_dir)
    adapter = adapter or get_provider(manifest["provider"])
    _verify_prepared_artifacts(run_dir, manifest)
    if manifest.get("execution", "batch") == "sync":
        return _execute_sync_requests(run_dir, manifest, adapter)
    with RunLock(run_dir):
        state = load_state(run_dir)
        if state.get("status") in {"completed", "completed_with_failures", "cancelled"}:
            return state
        for segment in state["segments"]:
            if segment["remote_batch_id"]:
                continue
            try:
                handle = adapter.submit(run_dir / segment["request_file"])
                segment.update(status="submitted", remote_batch_id=handle.batch_id, input_file_id=handle.input_file_id, provider_status=handle.status, error=None)
            except Exception as exc:
                segment.update(status="failed", error=str(exc))
                save_state(run_dir, state)
                raise
            save_state(run_dir, state)
        if any(segment["status"] == "submitted" for segment in state["segments"]):
            state["status"] = "submitted"
            state["stage"] = "submitted"
        save_state(run_dir, state)
        return state


def status_run(run: str | Path, adapter: ProviderAdapter | None = None) -> dict:
    run_dir = resolve_run(run)
    manifest = _manifest(run_dir)
    adapter = adapter or get_provider(manifest["provider"])
    with RunLock(run_dir):
        state = load_state(run_dir)
        for segment in state["segments"]:
            if not segment["remote_batch_id"] or segment["status"] in {"downloaded", "processed"}:
                continue
            remote = adapter.status(segment["remote_batch_id"])
            segment["provider_status"] = remote["provider_status"]
            segment["status"] = "completed" if remote["state"] == "completed" else remote["state"]
            segment["error"] = None if remote["state"] != "failed" else f"provider status: {remote['provider_status']}"
        statuses = {segment["status"] for segment in state["segments"]}
        if statuses == {"prepared"}:
            state["status"] = "prepared"
        elif statuses == {"processed"}:
            # Preserve the audited terminal outcome, including partial failure.
            if state.get("status") not in {"completed", "completed_with_failures"}:
                state["status"] = "completed"
        elif statuses <= {"completed", "downloaded", "processed"}:
            state["status"] = "completed"
        elif statuses == {"failed"}:
            state["status"] = "failed"
        else:
            state["status"] = "running"
        if state["status"] in {"submitted", "running", "completed"} and statuses != {"processed"}:
            state["stage"] = "running"
        save_state(run_dir, state)
        return state


def cancel_run(run: str | Path, adapter: ProviderAdapter | None = None) -> dict:
    run_dir = resolve_run(run)
    manifest = _manifest(run_dir)
    adapter = adapter or get_provider(manifest["provider"])
    with RunLock(run_dir):
        state = load_state(run_dir)
        for segment in state["segments"]:
            if segment["remote_batch_id"] and segment["status"] in {"submitted", "running"}:
                adapter.cancel(segment["remote_batch_id"])
                segment["status"] = "cancelled"
        state["status"] = "cancelled"
        save_state(run_dir, state)
        return state


def sync_run(run: str | Path, *, watch: bool = False, poll_seconds: int = 60, adapter: ProviderAdapter | None = None) -> dict:
    run_dir = resolve_run(run)
    manifest = _manifest(run_dir)
    adapter = adapter or get_provider(manifest["provider"])
    (run_dir / "raw").mkdir(exist_ok=True)
    while True:
        state = status_run(run_dir, adapter)
        with RunLock(run_dir):
            state = load_state(run_dir)
            for segment in state["segments"]:
                if segment["status"] != "completed":
                    continue
                index = segment["index"]
                output = run_dir / "raw" / f"segment_{index:04d}_output.jsonl"
                errors = run_dir / "raw" / f"segment_{index:04d}_errors.jsonl"
                if not output.exists():
                    adapter.download(segment["remote_batch_id"], output, errors)
                segment["status"] = "downloaded"
            if any(segment["status"] == "downloaded" for segment in state["segments"]):
                state["stage"] = "downloaded"
            save_state(run_dir, state)
        _process_downloads(run_dir, adapter)
        state = load_state(run_dir)
        unfinished = any(segment["status"] in {"submitted", "running"} for segment in state["segments"])
        if not watch or not unfinished:
            return state
        time.sleep(poll_seconds)


def _process_downloads(run_dir: Path, adapter: ProviderAdapter) -> None:
    manifest = _manifest(run_dir)
    _verify_prepared_artifacts(run_dir, manifest)
    with RunLock(run_dir):
        state = load_state(run_dir)
        if not any(segment["status"] == "downloaded" for segment in state["segments"]):
            return
        (run_dir / "results").mkdir(exist_ok=True)
        (run_dir / "reports").mkdir(exist_ok=True)
        row_schema = json.loads((run_dir / "snapshot" / "schema.json").read_text(encoding="utf-8"))
        validator = Draft202012Validator(row_schema)
        canonical = pd.read_parquet(_canonical_input_path(run_dir))
        canonical_by_id = {str(row["record_id"]): row.to_dict() for _, row in canonical.iterrows()}
        results: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        usage_by_request: dict[str, dict[str, Any]] = {}
        for segment in state["segments"]:
            if segment["status"] not in {"downloaded", "processed"}:
                continue
            mapping = {}
            for line in (run_dir / segment["mapping_file"]).read_text(encoding="utf-8").splitlines():
                item = json.loads(line)
                if "segment_index" in item and item["segment_index"] != segment["index"]:
                    continue
                mapping[item["custom_id"]] = item["record_ids"]
            output_path = run_dir / "raw" / f"segment_{segment['index']:04d}_output.jsonl"
            seen_requests: set[str] = set()
            raw_paths = [output_path, run_dir / "raw" / f"segment_{segment['index']:04d}_errors.jsonl"]
            for raw_path in raw_paths:
                if not raw_path.exists():
                    continue
                for line in raw_path.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    raw_item = json.loads(line)
                    normalized = (
                        NormalizedResult.model_validate(raw_item["_kllm_normalized"])
                        if "_kllm_normalized" in raw_item
                        else adapter.normalize_line(raw_item)
                    )
                    seen_requests.add(normalized.custom_id)
                    usage_by_request[normalized.custom_id] = {"custom_id": normalized.custom_id, "batch_id": segment["remote_batch_id"], "model_returned": normalized.model, "input_tokens": normalized.input_tokens, "output_tokens": normalized.output_tokens, "status": normalized.status}
                    expected = mapping.get(normalized.custom_id, [])
                    if normalized.status != "succeeded":
                        for record_id in expected:
                            failures.append(_failure(record_id, normalized.custom_id, normalized.error_type or normalized.status, normalized.error_message))
                        continue
                    try:
                        parsed = json.loads(normalized.response_text or "")
                    except json.JSONDecodeError as exc:
                        for record_id in expected:
                            failures.append(_failure(record_id, normalized.custom_id, "malformed_output", str(exc)))
                        continue
                    items = parsed.get("results") if isinstance(parsed, dict) else None
                    if not isinstance(items, list):
                        for record_id in expected:
                            failures.append(_failure(record_id, normalized.custom_id, "malformed_output", "missing results array"))
                        continue
                    returned_ids: list[str] = []
                    valid_by_id: dict[str, dict[str, Any]] = {}
                    for item in items:
                        record_id = str(item.get("record_id", "")) if isinstance(item, dict) else ""
                        returned_ids.append(record_id)
                        prediction = {k: v for k, v in item.items() if k != "record_id"} if isinstance(item, dict) else {}
                        errors = list(validator.iter_errors(prediction))
                        if record_id not in expected:
                            failures.append(_failure(record_id, normalized.custom_id, "unexpected_record_id", "record ID was not in this request"))
                        elif returned_ids.count(record_id) > 1:
                            failures.append(_failure(record_id, normalized.custom_id, "duplicate_output_id", "record ID appeared more than once"))
                        elif errors:
                            failures.append(_failure(record_id, normalized.custom_id, "schema_violation", errors[0].message))
                        else:
                            valid_by_id[record_id] = prediction
                    for record_id in expected:
                        if record_id not in valid_by_id:
                            if record_id not in returned_ids:
                                failures.append(_failure(record_id, normalized.custom_id, "missing_output", "no valid result returned"))
                            continue
                        source = canonical_by_id[record_id]
                        row_provenance = _row_provenance(manifest, source, normalized.custom_id, segment["remote_batch_id"], normalized.model, normalized.input_tokens, normalized.output_tokens, "valid")
                        row = {**source, **valid_by_id[record_id], **row_provenance}
                        results.append(row)
            for custom_id, expected in mapping.items():
                if custom_id not in seen_requests:
                    for record_id in expected:
                        failures.append(_failure(record_id, custom_id, "missing_request_output", "provider returned no line for request"))
            segment["status"] = "processed"
        batch_by_custom_id = {
            custom_id: segment["remote_batch_id"]
            for segment in state["segments"]
            for custom_id in _mapping_custom_ids(run_dir / segment["mapping_file"], segment["index"])
        }
        enriched_failures = []
        for failure in failures:
            record_id = str(failure["record_id"])
            custom_id = str(failure["custom_id"])
            request_usage = usage_by_request.get(custom_id, {}) or {}
            source = canonical_by_id.get(record_id, {"record_id": record_id, "source_row": None})
            row_provenance = _row_provenance(manifest, source, custom_id, batch_by_custom_id.get(custom_id), request_usage.get("model_returned"), int(request_usage.get("input_tokens", 0)), int(request_usage.get("output_tokens", 0)), "invalid")
            enriched_failures.append({**failure, **row_provenance})
        output_options = manifest["output_options"]
        _write_result_outputs(run_dir, results, output_options)
        _write_failures(run_dir, enriched_failures)
        atomic_write_json(run_dir / "reports" / "usage.json", {"requests": list(usage_by_request.values()), "input_tokens": sum(item["input_tokens"] for item in usage_by_request.values()), "output_tokens": sum(item["output_tokens"] for item in usage_by_request.values())})
        unfinished = any(segment["status"] in {"prepared", "submitted", "running", "completed", "downloaded"} for segment in state["segments"])
        state["status"] = "running" if unfinished else "completed_with_failures" if failures else "completed"
        state["stage"] = "processed"
        save_state(run_dir, state)
    if not unfinished:
        report = audit_run(run_dir)
        _write_run_summary(run_dir, report)


def _failure(record_id: str, custom_id: str, category: str, message: str | None) -> dict[str, Any]:
    return {"record_id": record_id, "custom_id": custom_id, "category": category, "message": message or ""}


def _mapping_custom_ids(path: Path, segment_index: int | None = None) -> list[str]:
    custom_ids = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        if segment_index is not None and "segment_index" in item and item["segment_index"] != segment_index:
            continue
        custom_ids.append(item["custom_id"])
    return custom_ids


def _row_provenance(
    manifest: dict[str, Any],
    source: dict[str, Any],
    custom_id: str,
    batch_id: str | None,
    model_returned: str | None,
    input_tokens: int,
    output_tokens: int,
    validation_status: str,
) -> dict[str, Any]:
    estimate = manifest["cost_estimate"]
    actual_cost = None
    if estimate.get("input_price_per_million") is not None and estimate.get("output_price_per_million") is not None:
        actual_cost = input_tokens / 1_000_000 * estimate["input_price_per_million"] + output_tokens / 1_000_000 * estimate["output_price_per_million"]
    source_row = source.get("source_row")
    if hasattr(source_row, "item"):
        source_row = source_row.item()
    return {
        "record_id": str(source["record_id"]),
        "source_row": source_row,
        "_kllm_source_row_sha256": source.get("_kllm_source_row_sha256"),
        "_kllm_truncated_fields": source.get("_kllm_truncated_fields", "{}"),
        "run_id": manifest["run_id"],
        "parent_run": manifest.get("parent_run"),
        "provider": manifest["provider"],
        "model_requested": manifest["model_requested"],
        "model_returned": model_returned,
        "prompt_version": manifest["prompt_version"],
        "custom_id": custom_id,
        "batch_id": batch_id,
        "validated_at": utc_now(),
        "input_tokens_request": input_tokens,
        "output_tokens_request": output_tokens,
        "actual_request_cost_usd": actual_cost,
        "validation_status": validation_status,
    }


def _write_result_outputs(run_dir: Path, rows: list[dict[str, Any]], output_options: dict[str, Any]) -> None:
    (run_dir / "results").mkdir(exist_ok=True)
    frame = pd.DataFrame(rows, columns=None if rows else ["record_id", "source_row"])
    if output_options["write_parquet"]:
        frame.to_parquet(run_dir / "results" / "results.parquet", index=False)
    if output_options["write_csv"]:
        frame.to_csv(run_dir / "results" / "results.csv", index=False)


def _write_failures(run_dir: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    with (run_dir / "results" / "failures.jsonl").open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, default=json_default) + "\n")


def _write_run_summary(run_dir: Path, audit: dict[str, Any]) -> None:
    (run_dir / "reports").mkdir(exist_ok=True)
    manifest = _manifest(run_dir)
    usage_path = run_dir / "reports" / "usage.json"
    usage = json.loads(usage_path.read_text()) if usage_path.exists() else {"input_tokens": 0, "output_tokens": 0}
    cost = manifest["cost_estimate"]
    actual_usd = None
    if cost.get("input_price_per_million") is not None and cost.get("output_price_per_million") is not None:
        actual_usd = usage["input_tokens"] / 1_000_000 * cost["input_price_per_million"] + usage["output_tokens"] / 1_000_000 * cost["output_price_per_million"]
    evaluation_metrics = _evaluation_metrics(run_dir, manifest)
    summary = {"run_id": manifest["run_id"], "generated_at": utc_now(), "project": manifest["project"], "purpose": manifest.get("purpose", "production"), "execution": manifest.get("execution", "batch"), "selection": manifest.get("selection", {"method": "all"}), "provider": manifest["provider"], "model_requested": manifest["model_requested"], "source_path": manifest["source_path"], "source_sha256": manifest["source_sha256"], "prompt_version": manifest["prompt_version"], "expected_records": audit["expected_records"], "valid_records": audit["valid_records"], "missing_records": len(audit["missing_record_ids"]), "input_tokens": usage["input_tokens"], "output_tokens": usage["output_tokens"], "estimated_maximum_usd": cost["estimated_usd"], "actual_usage_cost_usd": actual_usd, "pricing_as_of": cost.get("pricing_as_of"), "evaluation_metrics": evaluation_metrics, "results_parquet": str(run_dir / "results" / "results.parquet"), "results_csv": str(run_dir / "results" / "results.csv"), "failures_jsonl": str(run_dir / "results" / "failures.jsonl") if (run_dir / "results" / "failures.jsonl").exists() else None}
    atomic_write_json(run_dir / "reports" / "run_summary.json", summary)
    (run_dir / "reports" / "run_summary.md").write_text("# Run summary\n\n" + "\n".join(f"- **{key}**: {value}" for key, value in summary.items()) + "\n", encoding="utf-8")


def _evaluation_metrics(run_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    relative_path = manifest.get("gold_labels_file")
    results_path = run_dir / "results" / "results.parquet"
    if not relative_path or not results_path.exists():
        return {}
    gold = pd.read_parquet(run_dir / relative_path)
    results = pd.read_parquet(results_path)
    joined = gold.merge(results, on="record_id", how="inner", suffixes=("_gold", "_prediction"))
    metrics: dict[str, Any] = {}
    for field in manifest.get("gold_columns", {}):
        gold_field = f"{field}_gold"
        prediction_field = f"{field}_prediction"
        if gold_field not in joined.columns or prediction_field not in joined.columns:
            metrics[field] = {"n": 0, "accuracy": None, "error": "prediction field missing"}
            continue
        agreement = joined[gold_field].fillna("<NULL>").astype(str) == joined[prediction_field].fillna("<NULL>").astype(str)
        metrics[field] = {"n": len(agreement), "accuracy": float(agreement.mean()) if len(agreement) else None}
    return metrics


def audit_run(run: str | Path) -> dict[str, Any]:
    run_dir = resolve_run(run)
    (run_dir / "reports").mkdir(exist_ok=True)
    expected = set(pd.read_parquet(_canonical_input_path(run_dir))["record_id"].astype(str))
    results_path = run_dir / "results" / "results.parquet"
    actual_list = pd.read_parquet(results_path)["record_id"].astype(str).tolist() if results_path.exists() else []
    counts = Counter(actual_list)
    actual = set(actual_list)
    report = {
        "run_id": _manifest(run_dir)["run_id"],
        "generated_at": utc_now(),
        "expected_records": len(expected),
        "valid_records": len(actual),
        "missing_record_ids": sorted(expected - actual),
        "unexpected_record_ids": sorted(actual - expected),
        "duplicate_result_ids": sorted(record_id for record_id, count in counts.items() if count > 1),
        "complete": expected == actual and len(actual_list) == len(actual),
    }
    atomic_write_json(run_dir / "reports" / "audit.json", report)
    (run_dir / "reports" / "audit.md").write_text(
        f"# Audit: {report['run_id']}\n\nExpected: {len(expected)}  \nValid: {len(actual)}  \nMissing: {len(report['missing_record_ids'])}  \nUnexpected: {len(report['unexpected_record_ids'])}  \nComplete: {report['complete']}\n",
        encoding="utf-8",
    )
    with RunLock(run_dir):
        state = load_state(run_dir)
        state["stage"] = "audited"
        save_state(run_dir, state)
    return report


def prepare_retry(run: str | Path) -> Path:
    run_dir = resolve_run(run)
    failures_jsonl = run_dir / "results" / "failures.jsonl"
    legacy_parquet = run_dir / "results" / "failures.parquet"
    if failures_jsonl.exists():
        failures = pd.DataFrame(ProviderAdapter.read_jsonl(failures_jsonl))
    elif legacy_parquet.exists():
        failures = pd.read_parquet(legacy_parquet)
    else:
        raise FileNotFoundError("No failures were recorded; sync and audit the run first")
    if failures.empty:
        raise ValueError("The run has no failed records to retry")
    retryable = {"errored", "expired", "cancelled", "unknown", "malformed_output", "schema_violation", "missing_output", "missing_request_output", "duplicate_output_id"}
    selected = set(failures.loc[failures["category"].isin(retryable), "record_id"].astype(str))
    if not selected:
        raise ValueError("No retryable failed records were found")
    manifest = _manifest(run_dir)
    if sha256_file(Path(manifest["source_path"])) != manifest["source_sha256"]:
        raise ValueError("Source data changed after the parent run; restore the original source snapshot or start a new run")
    return prepare_run(
        manifest["config_path"],
        manifest["provider"],
        execution=manifest.get("execution", "batch"),
        selected_ids=selected,
        parent_run=str(run_dir),
    )


def merge_run(run: str | Path) -> Path:
    run_dir = resolve_run(run)
    chain: list[Path] = []
    current: Path | None = run_dir
    while current is not None:
        chain.append(current)
        parent = _manifest(current).get("parent_run")
        current = resolve_run(parent) if parent else None
    frames = []
    for item in reversed(chain):
        path = item / "results" / "results.parquet"
        if path.exists():
            frames.append(pd.read_parquet(path))
    if not frames:
        raise FileNotFoundError("No processed results found in the run chain")
    merged = pd.concat(frames, ignore_index=True).drop_duplicates("record_id", keep="last").sort_values("record_id")
    output = run_dir / "results" / "merged.parquet"
    merged.to_parquet(output, index=False)
    if _manifest(run_dir)["output_options"]["write_csv"]:
        merged.to_csv(run_dir / "results" / "merged.csv", index=False)
    return output


def compare_runs(run_a: str | Path, run_b: str | Path) -> dict[str, Any]:
    left_dir, right_dir = resolve_run(run_a), resolve_run(run_b)
    left = pd.read_parquet(left_dir / "results" / "results.parquet")
    right = pd.read_parquet(right_dir / "results" / "results.parquet")
    left_manifest, right_manifest = _manifest(left_dir), _manifest(right_dir)
    schema = json.loads((left_dir / "snapshot" / "schema.json").read_text(encoding="utf-8"))
    fields = [name for name, spec in schema.get("properties", {}).items() if "enum" in spec]
    joined = left[["record_id", *[f for f in fields if f in left]]].merge(right[["record_id", *[f for f in fields if f in right]]], on="record_id", suffixes=("_a", "_b"), how="outer", indicator=True)
    metrics: dict[str, Any] = {}
    disagreement_mask = joined["_merge"] != "both"
    for field in fields:
        a, b = f"{field}_a", f"{field}_b"
        if a not in joined or b not in joined:
            continue
        both = joined[joined["_merge"] == "both"]
        agreement = (both[a].fillna("<NULL>") == both[b].fillna("<NULL>"))
        metrics[field] = {"n_compared": len(both), "percent_agreement": float(agreement.mean()) if len(both) else None, "cohens_kappa": _cohens_kappa(both[a], both[b]) if len(both) else None}
        disagreement_mask |= joined[a].fillna("<NULL>") != joined[b].fillna("<NULL>")
    output_dir = left_dir.parent / f"comparison_{left_manifest['run_id']}_vs_{right_manifest['run_id']}"
    output_dir.mkdir(exist_ok=True)
    disagreements = joined[disagreement_mask]
    disagreements.to_csv(output_dir / "disagreements.csv", index=False)
    report = {"run_a": str(left_dir), "run_b": str(right_dir), "generated_at": utc_now(), "shared_records": int((joined["_merge"] == "both").sum()), "only_run_a": int((joined["_merge"] == "left_only").sum()), "only_run_b": int((joined["_merge"] == "right_only").sum()), "metrics": metrics, "disagreement_rows": len(disagreements)}
    atomic_write_json(output_dir / "comparison.json", report)
    return report


def _cohens_kappa(a: pd.Series, b: pd.Series) -> float | None:
    a = a.fillna("<NULL>").astype(str)
    b = b.fillna("<NULL>").astype(str)
    if len(a) == 0:
        return None
    observed = float((a == b).mean())
    labels = set(a) | set(b)
    expected = sum(float((a == label).mean()) * float((b == label).mean()) for label in labels)
    return None if math.isclose(expected, 1.0) else (observed - expected) / (1 - expected)
