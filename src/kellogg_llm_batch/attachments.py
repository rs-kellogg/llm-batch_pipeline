"""Opt-in, local attachment of one PNG or PDF to each prepared request."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from .data import infer_format, read_table
from .providers.base import ProviderAdapter
from .state import load_state, resolve_run
from .utils import RunLock, atomic_write_json, sha256_file, utc_now


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_ANTHROPIC_REQUEST_BYTES = 32_000_000
MAX_ANTHROPIC_IMAGE_BASE64_BYTES = 10_000_000
MAX_OPENAI_PDF_BYTES = 50_000_000


def request_file_for_segment(run_dir: Path, manifest: dict[str, Any], segment: dict[str, Any]) -> Path:
    """Select attached requests only when an explicit ready marker is present."""
    attachments = manifest.get("file_attachments")
    if attachments and attachments.get("status") == "required":
        raise ValueError("This retry needs files attached before submission; run 'kllm-batch attach-files'.")
    if attachments and attachments.get("status") == "ready":
        return run_dir / attachments["request_files"][str(segment["index"])]
    if attachments:
        raise ValueError("Unknown file attachment state; review the run manifest before submission")
    return run_dir / segment["request_file"]


def _verify_prepared(run_dir: Path, manifest: dict[str, Any]) -> None:
    for relative, expected in (manifest.get("prepared_artifact_sha256") or {}).items():
        path = run_dir / relative
        if not path.is_file() or sha256_file(path) != expected:
            raise ValueError(f"Prepared artifact changed after review: {path}. Prepare a new run.")


def _source_rows(run_dir: Path, manifest: dict[str, Any], column: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    source = Path(manifest["source_path"])
    if not source.is_file() or sha256_file(source) != manifest["source_sha256"]:
        raise ValueError("Source CSV changed after preparation; restore it or prepare a new run")
    snapshot = yaml.safe_load((run_dir / "project_snapshot" / "project.yaml").read_text(encoding="utf-8"))
    input_settings = snapshot["input"]
    fmt = infer_format(source, input_settings.get("format", "auto"))
    if fmt != "csv":
        raise ValueError("attach-files currently requires a CSV input")
    frame = read_table(source, fmt, input_settings.get("csv_encoding", "utf-8"))
    if column not in frame.columns:
        raise ValueError(f"Attachment column {column!r} was not found in the source CSV")
    if column in input_settings.get("fields_sent", {}).values():
        raise ValueError("The attachment filename column must not be in input.fields_sent")
    return frame, snapshot


def _attachment_path(root: Path, value: Any, record_id: str) -> tuple[Path, str]:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Record {record_id!r} has no attachment filename")
    name = value.strip()
    relative = Path(name)
    if relative.is_absolute() or ".." in relative.parts or "\\" in name or ":" in name:
        raise ValueError(f"Record {record_id!r} has an unsafe attachment filename: {name!r}")
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError(f"Record {record_id!r} attachment is missing or outside {root}: {name!r}")
    return path, relative.as_posix()


def _media_type(path: Path, data: bytes, record_id: str) -> str:
    suffix = path.suffix.lower()
    if suffix == ".png" and data.startswith(PNG_SIGNATURE):
        return "image/png"
    if suffix == ".pdf" and data.startswith(b"%PDF-"):
        return "application/pdf"
    raise ValueError(f"Record {record_id!r} must reference a valid .png or .pdf file")


def _patch_payload(payload: dict[str, Any], provider: str, media_type: str, filename: str, data: bytes) -> dict[str, Any]:
    result = copy.deepcopy(payload)
    encoded = base64.b64encode(data).decode("ascii")
    if provider == "openai":
        prompt = result["body"]["input"]
        if not isinstance(prompt, str):
            raise ValueError("OpenAI prepared request is not a text-only Responses request")
        file_part = (
            {"type": "input_image", "image_url": f"data:image/png;base64,{encoded}"}
            if media_type == "image/png"
            else {"type": "input_file", "filename": filename, "file_data": f"data:application/pdf;base64,{encoded}"}
        )
        result["body"]["input"] = [{"role": "user", "content": [file_part, {"type": "input_text", "text": prompt}]}]
    elif provider == "anthropic":
        messages = result["params"]["messages"]
        if len(messages) != 1 or messages[0].get("role") != "user" or not isinstance(messages[0].get("content"), str):
            raise ValueError("Anthropic prepared request is not a single text-only user message")
        prompt = messages[0]["content"]
        file_part = {
            "type": "image" if media_type == "image/png" else "document",
            "source": {"type": "base64", "media_type": media_type, "data": encoded},
        }
        messages[0]["content"] = [file_part, {"type": "text", "text": prompt}]
    else:
        raise ValueError(f"Unsupported provider for attachments: {provider}")
    return result


def _json_line(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def attach_files_to_run(
    run: str | Path,
    *,
    column: str,
    files_dir: str | Path,
    acknowledge_unestimated_cost: bool,
) -> Path:
    """Create provider-native attached requests without contacting a provider."""
    if not acknowledge_unestimated_cost:
        raise ValueError("File input cost is not estimated; pass --acknowledge-unestimated-cost to continue")
    run_dir = resolve_run(run)
    with RunLock(run_dir):
        manifest_path = run_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        state = load_state(run_dir)
        prior = manifest.get("file_attachments") or {}
        if prior.get("status") == "ready":
            raise ValueError("Files are already attached to this run; prepare a new run to change them")
        if state.get("status") != "prepared" or any(
            item.get("status") != "prepared" or item.get("remote_batch_id")
            for item in state["segments"]
        ):
            raise ValueError("Files can only be attached to an unsubmitted prepared run")
        if (run_dir / "raw_responses").exists():
            raise ValueError("Files cannot be attached after synchronous execution has started")
        _verify_prepared(run_dir, manifest)
        frame, snapshot = _source_rows(run_dir, manifest, column)
        base_dir = Path(manifest["config_path"]).parent
        requested_root = Path(files_dir).expanduser()
        root = (requested_root if requested_root.is_absolute() else base_dir / requested_root).resolve()
        if not root.is_dir():
            raise ValueError(f"Attachment directory not found: {root}")
        if prior.get("status") == "required":
            if column != prior["column"] or str(root) != prior["files_directory"]:
                raise ValueError("Retry attachments must use the parent's column and files directory")

        canonical = pd.read_parquet(run_dir / "input_snapshot" / "canonical_input.parquet")
        row_by_id = {str(row["record_id"]): int(row["source_row"]) for _, row in canonical.iterrows()}
        mappings = ProviderAdapter.read_jsonl(run_dir / "input_snapshot" / "request_map.jsonl")
        id_by_custom = {}
        for item in mappings:
            ids = item["record_ids"]
            if len(ids) != 1:
                raise ValueError("attach-files requires exactly one CSV row per prepared request; set rows_per_request: 1")
            id_by_custom[item["custom_id"]] = str(ids[0])
        if len(id_by_custom) != len(mappings) or set(id_by_custom.values()) != set(row_by_id):
            raise ValueError("Prepared request mapping does not match selected CSV records")

        provider = manifest["provider"]
        settings = snapshot["providers"][provider]
        default_max = 200_000_000 if provider == "openai" else 256_000_000
        segment_limit = settings.get("max_batch_bytes") or default_max
        request_files: dict[str, str] = {}
        file_rows: list[dict[str, Any]] = []
        seen_custom: set[str] = set()
        with tempfile.TemporaryDirectory(prefix=".attachments-", dir=run_dir / "api_requests") as temporary:
            temporary_dir = Path(temporary)
            staged: list[tuple[Path, Path]] = []
            for segment in state["segments"]:
                original = run_dir / segment["request_file"]
                patched: list[bytes] = []
                for payload in ProviderAdapter.read_jsonl(original):
                    custom_id = str(payload["custom_id"])
                    if custom_id in seen_custom or custom_id not in id_by_custom:
                        raise ValueError(f"Unexpected or duplicate prepared request ID: {custom_id}")
                    seen_custom.add(custom_id)
                    record_id = id_by_custom[custom_id]
                    source_row = row_by_id[record_id]
                    path, relative = _attachment_path(root, frame.iloc[source_row][column], record_id)
                    file_size = path.stat().st_size
                    encoded_size = 4 * ((file_size + 2) // 3)
                    if provider == "openai" and path.suffix.lower() == ".pdf" and file_size >= MAX_OPENAI_PDF_BYTES:
                        raise ValueError(f"Record {record_id!r} PDF exceeds OpenAI's 50 MB file limit")
                    if provider == "anthropic" and path.suffix.lower() == ".png" and encoded_size >= MAX_ANTHROPIC_IMAGE_BASE64_BYTES:
                        raise ValueError(f"Record {record_id!r} PNG exceeds Anthropic's 10 MB encoded-image limit")
                    if provider == "anthropic" and encoded_size >= MAX_ANTHROPIC_REQUEST_BYTES:
                        raise ValueError(f"Record {record_id!r} exceeds Anthropic's 32 MB request limit")
                    if encoded_size >= segment_limit:
                        raise ValueError(f"Record {record_id!r} exceeds the configured batch segment size")
                    data = path.read_bytes()
                    if len(data) != file_size:
                        raise ValueError(f"Record {record_id!r} attachment changed while being read")
                    media_type = _media_type(path, data, record_id)
                    digest = hashlib.sha256(data).hexdigest()
                    expected = (prior.get("expected_hashes") or {}).get(record_id)
                    if expected is not None and digest != expected:
                        raise ValueError(f"Record {record_id!r} attachment changed since the parent run")
                    encoded_payload = _json_line(_patch_payload(payload, provider, media_type, path.name, data))
                    if provider == "anthropic" and len(encoded_payload) >= MAX_ANTHROPIC_REQUEST_BYTES:
                        raise ValueError(f"Record {record_id!r} exceeds Anthropic's 32 MB request limit")
                    if len(encoded_payload) > segment_limit:
                        raise ValueError(f"Record {record_id!r} exceeds the configured batch segment size")
                    patched.append(encoded_payload)
                    file_rows.append({"record_id": record_id, "source_row": source_row, "path": relative, "sha256": digest, "bytes": len(data), "media_type": media_type})
                if sum(map(len, patched)) > segment_limit:
                    raise ValueError(f"Attached segment {segment['index']} exceeds the configured batch segment size")
                filename = f"attached_segment_{segment['index']:04d}.jsonl"
                staged_path = temporary_dir / filename
                staged_path.write_bytes(b"".join(patched))
                destination = run_dir / "api_requests" / filename
                staged.append((staged_path, destination))
                request_files[str(segment["index"])] = str(destination.relative_to(run_dir))
            if seen_custom != set(id_by_custom):
                raise ValueError("Some selected records have no prepared request")
            for staged_path, destination in staged:
                os.replace(staged_path, destination)

        manifest["file_attachments"] = {
            "status": "ready",
            "column": column,
            "files_directory": str(root),
            "created_at": utc_now(),
            "cost_estimate_scope": "text_only_excludes_files",
            "request_files": request_files,
            "files": file_rows,
        }
        for relative in request_files.values():
            manifest["prepared_artifact_sha256"][relative] = sha256_file(run_dir / relative)
        atomic_write_json(manifest_path, manifest)
        review_path = run_dir / "REVIEW.md"
        review = review_path.read_text(encoding="utf-8")
        review = review.replace("- Estimated maximum cost:", "- Text-only estimate (excludes files):", 1)
        review = review.replace("api_requests/segment_*.jsonl", "api_requests/attached_segment_*.jsonl")
        if provider == "openai":
            review = review.replace(
                "jq -r '.body.instructions, .body.input'",
                "jq -r '.body.instructions, (.body.input[0].content[] | select(.type==\"input_text\").text)'",
            )
        else:
            review = review.replace(
                "jq -r '.params.system, .params.messages[].content'",
                "jq -r '.params.system, (.params.messages[].content[] | select(.type==\"text\").text)'",
            )
        review += (
            "\n## Attached files\n\n"
            "Use `api_requests/attached_segment_*.jsonl` for final review and submission. "
            "These files embed the original PNG/PDF bytes. Total cost is unknown until "
            "provider usage is returned. Keep run artifacts private.\n"
        )
        review_path.write_text(review, encoding="utf-8")
    return run_dir
