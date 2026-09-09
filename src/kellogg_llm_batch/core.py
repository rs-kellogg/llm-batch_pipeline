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
from .data import canonicalize, load_source
from .models import CanonicalRequest
from .pricing import estimate_cost
from .prompts import load_context, load_prompt_files, render_user_prompt
from .providers.base import ProviderAdapter, get_provider
from .schema import load_row_schema, wrapped_schema
from .state import load_state, resolve_run, save_state
from .utils import RunLock, atomic_write_json, sha256_file, sha256_text, utc_now
from .validation import validate_project


def _json_line(obj: dict[str, Any]) -> bytes:
    return (json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def _manifest(run_dir: Path) -> dict[str, Any]:
    return json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))


def _build_requests(config: ProjectConfig, provider_name: str, selected_ids: set[str] | None = None):
    df, source, _ = load_source(config)
    records = canonicalize(config, df, source)
    if selected_ids is not None:
        records = [record for record in records if record.record_id in selected_ids]
        missing = selected_ids - {record.record_id for record in records}
        if missing:
            raise ValueError(f"Retry IDs no longer exist in source: {sorted(missing)[:20]}")
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


def prepare_run(config_or_path: ProjectConfig | str | Path, provider: str, *, selected_ids: set[str] | None = None, parent_run: str | None = None) -> Path:
    config = config_or_path if isinstance(config_or_path, ProjectConfig) else load_config(config_or_path)
    if provider not in config.providers:
        raise ValueError(f"Provider '{provider}' is not configured")
    validate_project(config)
    records, canonical, payloads, source, schema = _build_requests(config, provider, selected_ids)
    if not records:
        raise ValueError("No records selected for preparation")
    adapter = get_provider(provider)
    settings = config.providers[provider]
    max_requests = settings.max_requests_per_batch or adapter.default_max_requests
    max_bytes = settings.max_batch_bytes or adapter.default_max_bytes
    segments = _split_payloads(payloads, max_requests, max_bytes)
    estimate = estimate_cost(config, provider, [request.system_prompt + "\n" + request.user_prompt for request in canonical])
    if estimate.estimated_usd is None:
        raise ValueError(f"No pricing is configured for {provider}/{settings.model}; add provider price overrides")
    if estimate.estimated_usd > config.budget.max_estimated_usd:
        raise ValueError(f"Estimated cost ${estimate.estimated_usd:.4f} exceeds budget ${config.budget.max_estimated_usd:.4f}")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = f"{timestamp}_{provider}_{uuid.uuid4().hex[:8]}"
    runs_root = config.resolve(config.output.runs_directory)
    final_run_dir = runs_root / run_id
    run_dir = runs_root / f".{run_id}.building"
    for name in ("requests", "mappings", "raw", "results", "reports", "snapshot"):
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
    canonical_df.to_parquet(run_dir / "requests" / "canonical_input.parquet", index=False)
    if config.output.write_csv:
        canonical_df.to_csv(run_dir / "requests" / "canonical_input.csv", index=False)

    canonical_by_id = {item.custom_id: item for item in canonical}
    state_segments = []
    for index, segment in enumerate(segments):
        request_path = run_dir / "requests" / f"segment_{index:04d}.jsonl"
        mapping_path = run_dir / "mappings" / f"segment_{index:04d}.jsonl"
        request_path.write_bytes(b"".join(_json_line(item) for item in segment))
        with mapping_path.open("w", encoding="utf-8") as handle:
            for item in segment:
                request = canonical_by_id[item["custom_id"]]
                handle.write(json.dumps({"custom_id": request.custom_id, "record_ids": request.record_ids}) + "\n")
        state_segments.append({"index": index, "request_file": str(request_path.relative_to(run_dir)), "mapping_file": str(mapping_path.relative_to(run_dir)), "status": "prepared", "remote_batch_id": None, "input_file_id": None, "provider_status": None, "error": None})

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
        "pilot_random_seed": config.pilot.random_seed,
        "parent_run": parent_run,
        "cost_estimate": estimate.model_dump(),
        "output_options": config.output.model_dump(mode="json"),
        "context": context_manifest,
    }
    atomic_write_json(run_dir / "manifest.json", manifest)
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


def submit_run(run: str | Path, adapter: ProviderAdapter | None = None) -> dict:
    run_dir = resolve_run(run)
    manifest = _manifest(run_dir)
    adapter = adapter or get_provider(manifest["provider"])
    with RunLock(run_dir):
        state = load_state(run_dir)
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
    with RunLock(run_dir):
        state = load_state(run_dir)
        if not any(segment["status"] == "downloaded" for segment in state["segments"]):
            return
        manifest = _manifest(run_dir)
        row_schema = json.loads((run_dir / "snapshot" / "schema.json").read_text(encoding="utf-8"))
        validator = Draft202012Validator(row_schema)
        canonical = pd.read_parquet(run_dir / "requests" / "canonical_input.parquet")
        canonical_by_id = {str(row["record_id"]): row.to_dict() for _, row in canonical.iterrows()}
        results: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        provenance: list[dict[str, Any]] = []
        usage_by_request: dict[str, dict[str, Any]] = {}
        for segment in state["segments"]:
            if segment["status"] not in {"downloaded", "processed"}:
                continue
            mapping = {}
            for line in (run_dir / segment["mapping_file"]).read_text(encoding="utf-8").splitlines():
                item = json.loads(line)
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
                    normalized = adapter.normalize_line(json.loads(line))
                    seen_requests.add(normalized.custom_id)
                    usage_by_request[normalized.custom_id] = {"custom_id": normalized.custom_id, "batch_id": segment["remote_batch_id"], "input_tokens": normalized.input_tokens, "output_tokens": normalized.output_tokens, "status": normalized.status}
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
                        row = {**source, **valid_by_id[record_id], "provider": manifest["provider"], "model_requested": manifest["model_requested"], "model_returned": normalized.model, "run_id": manifest["run_id"], "custom_id": normalized.custom_id}
                        results.append(row)
                        provenance.append(_row_provenance(manifest, source, normalized.custom_id, segment["remote_batch_id"], normalized.model, normalized.input_tokens, normalized.output_tokens, "valid"))
            for custom_id, expected in mapping.items():
                if custom_id not in seen_requests:
                    for record_id in expected:
                        failures.append(_failure(record_id, custom_id, "missing_request_output", "provider returned no line for request"))
            segment["status"] = "processed"
        valid_ids = {str(row["record_id"]) for row in results}
        failure_categories: dict[str, set[str]] = {}
        failure_custom_ids: dict[str, str] = {}
        for failure in failures:
            record_id = str(failure["record_id"])
            if record_id in canonical_by_id and record_id not in valid_ids:
                failure_categories.setdefault(record_id, set()).add(str(failure["category"]))
                failure_custom_ids.setdefault(record_id, str(failure["custom_id"]))
        batch_by_custom_id = {
            custom_id: segment["remote_batch_id"]
            for segment in state["segments"]
            for custom_id in _mapping_custom_ids(run_dir / segment["mapping_file"])
        }
        for record_id, categories in failure_categories.items():
            custom_id = failure_custom_ids[record_id]
            request_usage = usage_by_request.get(custom_id, {}) or {}
            failure_row = _row_provenance(manifest, canonical_by_id[record_id], custom_id, batch_by_custom_id.get(custom_id), None, int(request_usage.get("input_tokens", 0)), int(request_usage.get("output_tokens", 0)), "invalid")
            failure_row["error_categories"] = sorted(categories)
            provenance.append(failure_row)
        output_options = manifest["output_options"]
        _write_table_outputs(run_dir, "results", results, output_options)
        _write_table_outputs(run_dir, "failures", failures, output_options)
        atomic_write_json(run_dir / "results" / "provenance.json", {"run_id": manifest["run_id"], "rows": provenance})
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


def _mapping_custom_ids(path: Path) -> list[str]:
    return [json.loads(line)["custom_id"] for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


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
        "source_row_sha256": source.get("_kllm_source_row_sha256"),
        "source_sha256": manifest["source_sha256"],
        "run_id": manifest["run_id"],
        "parent_run": manifest.get("parent_run"),
        "provider": manifest["provider"],
        "model_requested": manifest["model_requested"],
        "model_returned": model_returned,
        "provider_options": manifest.get("provider_options", {}),
        "prompt_version": manifest["prompt_version"],
        "system_prompt_sha256": manifest["system_prompt_sha256"],
        "user_prompt_sha256": manifest["user_prompt_sha256"],
        "schema_sha256": manifest["schema_sha256"],
        "config_sha256": manifest.get("config_sha256"),
        "custom_id": custom_id,
        "batch_id": batch_id,
        "run_created_at": manifest["created_at"],
        "validated_at": utc_now(),
        "input_tokens_request": input_tokens,
        "output_tokens_request": output_tokens,
        "actual_request_cost_usd": actual_cost,
        "estimated_run_cost_usd": estimate.get("estimated_usd"),
        "truncated_fields": source.get("_kllm_truncated_fields", "{}"),
        "validation_status": validation_status,
    }


def _write_table_outputs(run_dir: Path, stem: str, rows: list[dict[str, Any]], output_options: dict[str, Any]) -> None:
    default_columns = {
        "results": ["record_id", "source_row"],
        "failures": ["record_id", "custom_id", "category", "message"],
    }
    frame = pd.DataFrame(rows, columns=None if rows else default_columns.get(stem))
    if output_options["write_parquet"]:
        frame.to_parquet(run_dir / "results" / f"{stem}.parquet", index=False)
    if output_options["write_csv"]:
        frame.to_csv(run_dir / "results" / f"{stem}.csv", index=False)


def _write_run_summary(run_dir: Path, audit: dict[str, Any]) -> None:
    manifest = _manifest(run_dir)
    usage_path = run_dir / "reports" / "usage.json"
    usage = json.loads(usage_path.read_text()) if usage_path.exists() else {"input_tokens": 0, "output_tokens": 0}
    cost = manifest["cost_estimate"]
    actual_usd = None
    if cost.get("input_price_per_million") is not None and cost.get("output_price_per_million") is not None:
        actual_usd = usage["input_tokens"] / 1_000_000 * cost["input_price_per_million"] + usage["output_tokens"] / 1_000_000 * cost["output_price_per_million"]
    summary = {"run_id": manifest["run_id"], "generated_at": utc_now(), "project": manifest["project"], "provider": manifest["provider"], "model_requested": manifest["model_requested"], "source_path": manifest["source_path"], "source_sha256": manifest["source_sha256"], "prompt_version": manifest["prompt_version"], "expected_records": audit["expected_records"], "valid_records": audit["valid_records"], "missing_records": len(audit["missing_record_ids"]), "input_tokens": usage["input_tokens"], "output_tokens": usage["output_tokens"], "estimated_maximum_usd": cost["estimated_usd"], "actual_usage_cost_usd": actual_usd, "pricing_as_of": cost.get("pricing_as_of"), "results_path": str(run_dir / "results" / "results.parquet")}
    atomic_write_json(run_dir / "reports" / "run_summary.json", summary)
    (run_dir / "reports" / "run_summary.md").write_text("# Run summary\n\n" + "\n".join(f"- **{key}**: {value}" for key, value in summary.items()) + "\n", encoding="utf-8")


def audit_run(run: str | Path) -> dict[str, Any]:
    run_dir = resolve_run(run)
    expected = set(pd.read_parquet(run_dir / "requests" / "canonical_input.parquet")["record_id"].astype(str))
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
    failures_path = run_dir / "results" / "failures.parquet"
    if not failures_path.exists():
        raise FileNotFoundError("No failures.parquet exists; sync and audit the run first")
    failures = pd.read_parquet(failures_path)
    if failures.empty:
        raise ValueError("The run has no failed records to retry")
    retryable = {"errored", "expired", "cancelled", "unknown", "malformed_output", "schema_violation", "missing_output", "missing_request_output", "duplicate_output_id"}
    selected = set(failures.loc[failures["category"].isin(retryable), "record_id"].astype(str))
    if not selected:
        raise ValueError("No retryable failed records were found")
    manifest = _manifest(run_dir)
    if sha256_file(Path(manifest["source_path"])) != manifest["source_sha256"]:
        raise ValueError("Source data changed after the parent run; restore the original source snapshot or start a new run")
    return prepare_run(manifest["config_path"], manifest["provider"], selected_ids=selected, parent_run=str(run_dir))


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


def pilot_project(config_or_path: ProjectConfig | str | Path, provider: str, adapter: ProviderAdapter | None = None) -> dict[str, Any]:
    config = config_or_path if isinstance(config_or_path, ProjectConfig) else load_config(config_or_path)
    validate_project(config)
    records, canonical, payloads, source, _ = _build_requests(config, provider)
    sample_n = min(config.pilot.sample_size, len(records))
    sampled_ids = set(pd.Series([r.record_id for r in records]).sample(n=sample_n, random_state=config.pilot.random_seed).tolist())
    _, sample_requests, sample_payloads, _, _ = _build_requests(config, provider, sampled_ids)
    adapter = adapter or get_provider(provider)
    normalized = [adapter.run_sync(payload) for payload in sample_payloads]
    schema = wrapped_schema(load_row_schema(config))
    validator = Draft202012Validator(schema)
    predictions: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    request_by_id = {request.custom_id: request for request in sample_requests}
    for outcome in normalized:
        expected = set(request_by_id[outcome.custom_id].record_ids)
        if outcome.status != "succeeded":
            findings.append({"custom_id": outcome.custom_id, "code": outcome.error_type or outcome.status, "message": outcome.error_message})
            continue
        try:
            parsed = json.loads(outcome.response_text or "")
        except json.JSONDecodeError as exc:
            findings.append({"custom_id": outcome.custom_id, "code": "malformed_output", "message": str(exc)})
            continue
        errors = list(validator.iter_errors(parsed))
        if errors:
            findings.append({"custom_id": outcome.custom_id, "code": "schema_violation", "message": errors[0].message})
            continue
        items = parsed["results"]
        returned = [str(item["record_id"]) for item in items]
        if len(returned) != len(set(returned)) or set(returned) != expected:
            findings.append({"custom_id": outcome.custom_id, "code": "incomplete_ids", "message": f"expected {sorted(expected)}, returned {sorted(returned)}"})
            continue
        predictions.extend(items)
    metrics: dict[str, Any] = {}
    if config.pilot.gold_columns and predictions:
        source_frame, _, _ = load_source(config)
        canonical_all = canonicalize(config, source_frame, source)
        source_by_id = {record.record_id: source_frame.iloc[record.source_row] for record in canonical_all}
        for output_field, source_column in config.pilot.gold_columns.items():
            if source_column not in source_frame.columns:
                raise ValueError(f"Pilot gold column not found: {source_column}")
            pairs = [(str(source_by_id[str(item["record_id"])][source_column]), str(item.get(output_field))) for item in predictions if str(item["record_id"]) in source_by_id]
            metrics[output_field] = {"n": len(pairs), "accuracy": sum(gold == predicted for gold, predicted in pairs) / len(pairs) if pairs else None}
    full_estimate = validate_project(config)["cost_estimates"][provider]
    report = {
        "provider": provider,
        "model": config.providers[provider].model,
        "sample_records": sample_n,
        "valid_records": len(predictions),
        "request_count": len(sample_requests),
        "valid": not findings and len(predictions) == sample_n,
        "findings": findings,
        "metrics": metrics,
        "projected_full_run_cost": full_estimate,
        "actual_pilot_usage": {"input_tokens": sum(item.input_tokens for item in normalized), "output_tokens": sum(item.output_tokens for item in normalized)},
        "source_sha256": sha256_file(source),
        "generated_at": utc_now(),
    }
    pilots = config.base_dir / "pilots"
    pilots.mkdir(exist_ok=True)
    path = pilots / f"pilot_{provider}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    atomic_write_json(path, report)
    report["report_path"] = str(path)
    return report
