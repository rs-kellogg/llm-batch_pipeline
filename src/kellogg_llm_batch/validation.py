from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Iterable

from .config import ProjectConfig, load_config
from .data import canonicalize, load_source, record_content_key
from .models import AuditFinding, CanonicalRecord
from .pricing import estimate_cost
from .prompts import load_context, load_prompt_files, render_user_prompt, validate_template
from .providers.base import get_provider
from .schema import load_row_schema, wrapped_schema


class ProjectValidationError(ValueError):
    def __init__(self, report: dict):
        self.report = report
        messages = "; ".join(item["message"] for item in report["findings"] if item["severity"] == "error")
        super().__init__(messages or "Project validation failed")


def find_duplicate_records(records: Iterable[CanonicalRecord]) -> list[AuditFinding]:
    records = list(records)
    findings: list[AuditFinding] = []
    ids: dict[str, list[CanonicalRecord]] = defaultdict(list)
    contents: dict[str, list[CanonicalRecord]] = defaultdict(list)
    for record in records:
        ids[record.record_id].append(record)
        contents[record_content_key(record)].append(record)
    missing = [record for record in records if not record.record_id]
    if missing:
        findings.append(AuditFinding(severity="error", code="missing_record_id", message=f"{len(missing)} row(s) have missing or empty IDs", source_rows=[r.source_row for r in missing[:50]]))
    for record_id, group in ids.items():
        if record_id and len(group) > 1:
            findings.append(AuditFinding(severity="error", code="duplicate_record_id", message=f"Duplicate ID {record_id!r} occurs {len(group)} times", record_ids=[record_id], source_rows=[r.source_row for r in group]))
    for key, group in contents.items():
        if len(group) > 1:
            preview = key[:160] + ("..." if len(key) > 160 else "")
            findings.append(AuditFinding(severity="error", code="duplicate_content", message=f"Duplicate sent-field content occurs {len(group)} times: {preview}", record_ids=[r.record_id for r in group], source_rows=[r.source_row for r in group]))
    return findings


def validate_project(config_or_path: ProjectConfig | str | Path, report_path: str | Path | None = None) -> dict:
    """Validate a project and optionally persist diagnostics for every failure."""
    try:
        return _validate_project(config_or_path, report_path)
    except ProjectValidationError:
        raise
    except Exception as exc:
        report = {
            "valid": False,
            "project": getattr(getattr(config_or_path, "project", None), "name", "unknown"),
            "input_path": None,
            "input_format": None,
            "source_rows": 0,
            "canonical_rows": 0,
            "request_count": 0,
            "findings": [AuditFinding(severity="error", code="validation_error", message=str(exc)).model_dump()],
            "cost_estimates": {},
            "segment_estimates": {},
        }
        if report_path:
            destination = Path(report_path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        raise ProjectValidationError(report) from exc


def _validate_project(config_or_path: ProjectConfig | str | Path, report_path: str | Path | None = None) -> dict:
    config = config_or_path if isinstance(config_or_path, ProjectConfig) else load_config(config_or_path)
    findings: list[AuditFinding] = []
    df, source, fmt = load_source(config)
    records = canonicalize(config, df, source)
    if not records:
        findings.append(AuditFinding(severity="error", code="empty_input", message="Input contains no records"))
    findings.extend(find_duplicate_records(records))
    for record in records:
        missing = [field for field in config.input.required_fields if record.sent.get(field) is None]
        if missing:
            findings.append(AuditFinding(severity="error", code="missing_required_field", message=f"Source row {record.source_row} is missing required fields: {missing}", record_ids=[record.record_id] if record.record_id else [], source_rows=[record.source_row]))
    row_schema = load_row_schema(config)
    wrapped_schema(row_schema)
    schema_properties = set(row_schema.get("properties", {}))
    for output_field, source_column in config.evaluation.gold_columns.items():
        if source_column not in df.columns:
            findings.append(
                AuditFinding(
                    severity="error",
                    code="missing_gold_column",
                    message=f"Evaluation source column {source_column!r} was not found",
                )
            )
        if output_field not in schema_properties:
            findings.append(
                AuditFinding(
                    severity="error",
                    code="unknown_gold_output_field",
                    message=f"Evaluation output field {output_field!r} is not defined in schema.json",
                )
            )
    system_prompt, user_template = load_prompt_files(config)
    context = load_context(config)
    validate_template(user_template, context)
    request_texts = []
    prompt_records_by_request = []
    for start in range(0, len(records), config.task.rows_per_request):
        group = records[start : start + config.task.rows_per_request]
        prompt_records = [{"record_id": r.record_id, **r.sent} for r in group]
        user_prompt = render_user_prompt(user_template, prompt_records, context)
        request_texts.append(system_prompt + "\n" + user_prompt)
        prompt_records_by_request.append((system_prompt, user_prompt))
        estimated_tokens = max(1, (len(system_prompt) + len(user_prompt) + 3) // 4)
        if config.task.max_input_tokens and estimated_tokens > config.task.max_input_tokens:
            findings.append(AuditFinding(severity="error", code="request_context_limit", message=f"Request {len(request_texts) - 1} is approximately {estimated_tokens:,} input tokens, above task.max_input_tokens={config.task.max_input_tokens:,}"))
    estimates = {name: estimate_cost(config, name, request_texts).model_dump() for name in config.providers}
    segment_estimates = {}
    for provider_name, settings in config.providers.items():
        adapter = get_provider(provider_name)
        max_requests = settings.max_requests_per_batch or adapter.default_max_requests
        max_bytes = settings.max_batch_bytes or adapter.default_max_bytes
        sizes = []
        for index, (system, user) in enumerate(prompt_records_by_request):
            payload = adapter.build_payload(f"request_{index:08d}", settings.model, system, user, wrapped_schema(row_schema), config.task.max_output_tokens, settings.options)
            size = len((json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"))
            sizes.append(size)
            if size > max_bytes:
                findings.append(AuditFinding(severity="error", code="request_size_limit", message=f"{provider_name} request {index} is {size:,} bytes, above max_batch_bytes={max_bytes:,}"))
        segments = 0
        count = 0
        used_bytes = 0
        for size in sizes:
            if count and (count >= max_requests or used_bytes + size > max_bytes):
                segments += 1
                count = 0
                used_bytes = 0
            count += 1
            used_bytes += size
        if count:
            segments += 1
        segment_estimates[provider_name] = {"segments": segments, "request_bytes": sum(sizes), "max_requests_per_batch": max_requests, "max_batch_bytes": max_bytes}
    report = {
        "valid": not any(f.severity == "error" for f in findings),
        "project": config.project.name,
        "input_path": str(source),
        "input_format": fmt,
        "source_rows": len(df),
        "canonical_rows": len(records),
        "request_count": len(request_texts),
        "findings": [f.model_dump() for f in findings],
        "cost_estimates": estimates,
        "segment_estimates": segment_estimates,
    }
    if report_path:
        destination = Path(report_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if not report["valid"]:
        raise ProjectValidationError(report)
    return report
