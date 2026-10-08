from __future__ import annotations

import base64
import json
from pathlib import Path

import pandas as pd
import pytest
import yaml
from typer.testing import CliRunner

from conftest import FakeAdapter
import kellogg_llm_batch.attachments as attachment_module
import kellogg_llm_batch.core as core
from kellogg_llm_batch.attachments import attach_files_to_run
from kellogg_llm_batch.cli import app
from kellogg_llm_batch.core import extrapolate_cost_from_run, prepare_retry, prepare_run, submit_run, sync_checkpoint_progress
from kellogg_llm_batch.models import NormalizedResult
from kellogg_llm_batch.providers.base import ProviderAdapter
from kellogg_llm_batch.scaffold import scaffold_project
from kellogg_llm_batch.utils import sha256_file


PNG_BYTES = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvTqtsQA5w")
PDF_BYTES = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\n%%EOF\n"


def _project(tmp_path: Path) -> tuple[Path, Path]:
    root = scaffold_project(tmp_path / "project")
    config_path = root / "project.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["task"]["rows_per_request"] = 1
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    data_path = root / "data" / "input-data.csv"
    frame = pd.read_csv(data_path, dtype=str).iloc[:2].copy()
    frame["attachment_file"] = ["first.png", "second.pdf"]
    frame.to_csv(data_path, index=False)
    files_dir = root / "data" / "attachments"
    files_dir.mkdir()
    (files_dir / "first.png").write_bytes(PNG_BYTES)
    (files_dir / "second.pdf").write_bytes(PDF_BYTES)
    return config_path, files_dir


def _manifest(run: Path) -> dict:
    return json.loads((run / "manifest.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_attached_requests_preserve_original_and_submit_attached(tmp_path, provider):
    config, files_dir = _project(tmp_path)
    run = prepare_run(config, provider, execution="batch")
    original = (run / "api_requests" / "segment_0000.jsonl").read_bytes()
    before = _manifest(run)
    assert "file_attachments" not in before

    attach_files_to_run(run, column="attachment_file", files_dir=files_dir, acknowledge_unestimated_cost=True)
    after = _manifest(run)
    assert (run / "api_requests" / "segment_0000.jsonl").read_bytes() == original
    assert after["cost_estimate"] == before["cost_estimate"]
    assert after["file_attachments"]["cost_estimate_scope"] == "text_only_excludes_files"
    assert {item["record_id"] for item in after["file_attachments"]["files"]} == {"GRANT-001", "GRANT-002"}
    attached_path = run / after["file_attachments"]["request_files"]["0"]
    assert after["prepared_artifact_sha256"][str(attached_path.relative_to(run))] == sha256_file(attached_path)
    payloads = ProviderAdapter.read_jsonl(attached_path)
    original_payloads = ProviderAdapter.read_jsonl(run / "api_requests" / "segment_0000.jsonl")
    for index, (old, new) in enumerate(zip(original_payloads, payloads, strict=True)):
        if provider == "openai":
            assert new["body"]["instructions"] == old["body"]["instructions"]
            parts = new["body"]["input"][0]["content"]
            assert parts[1] == {"type": "input_text", "text": old["body"]["input"]}
            if index == 0:
                assert parts[0]["type"] == "input_image"
                encoded = parts[0]["image_url"].split(",", 1)[1]
                assert base64.b64decode(encoded) == PNG_BYTES
            else:
                assert parts[0]["type"] == "input_file"
                assert parts[0]["filename"] == "second.pdf"
                encoded = parts[0]["file_data"].split(",", 1)[1]
                assert base64.b64decode(encoded) == PDF_BYTES
        else:
            parts = new["params"]["messages"][0]["content"]
            assert parts[1] == {"type": "text", "text": old["params"]["messages"][0]["content"]}
            assert parts[0]["type"] == ("image" if index == 0 else "document")
            assert base64.b64decode(parts[0]["source"]["data"]) == (PNG_BYTES if index == 0 else PDF_BYTES)

    fake = FakeAdapter()
    submit_run(run, fake)
    assert fake.batches["fake-1"] == payloads
    assert _manifest(run)["file_attachments"]["status"] == "ready"


def test_text_only_request_and_manifest_remain_unchanged(tmp_path):
    config, _ = _project(tmp_path)
    run = prepare_run(config, "openai", execution="batch")
    manifest_before = (run / "manifest.json").read_bytes()
    request_before = (run / "api_requests" / "segment_0000.jsonl").read_bytes()
    fake = FakeAdapter()
    submit_run(run, fake)
    assert (run / "manifest.json").read_bytes() == manifest_before
    assert "file_attachments" not in _manifest(run)
    assert fake.batches["fake-1"] == ProviderAdapter.read_jsonl(run / "api_requests" / "segment_0000.jsonl")
    assert (run / "api_requests" / "segment_0000.jsonl").read_bytes() == request_before


def test_text_only_retry_needs_no_attachment_step(tmp_path):
    config, _ = _project(tmp_path)
    run = prepare_run(config, "openai", execution="batch")
    outputs = run / "outputs"
    outputs.mkdir()
    (outputs / "failures.jsonl").write_text(json.dumps({"record_id": "GRANT-001", "category": "errored"}) + "\n", encoding="utf-8")
    child = prepare_retry(run)
    assert "file_attachments" not in _manifest(child)
    fake = FakeAdapter()
    submit_run(child, fake)
    assert fake.submissions == 1


def test_attachment_pilot_selects_only_prepared_rows(tmp_path):
    config, files_dir = _project(tmp_path)
    run = prepare_run(config, "openai", sample_size=1, seed=3)
    attach_files_to_run(run, column="attachment_file", files_dir=files_dir, acknowledge_unestimated_cost=True)
    selected = pd.read_parquet(run / "input_snapshot" / "canonical_input.parquet")
    attachments = _manifest(run)["file_attachments"]["files"]
    assert len(attachments) == 1
    assert attachments[0]["record_id"] == selected.iloc[0]["record_id"]
    assert attachments[0]["source_row"] == selected.iloc[0]["source_row"]
    assert sync_checkpoint_progress(run)["total"] == 1


def test_attached_sync_pilot_processes_and_marks_cost_incomplete(tmp_path):
    config, files_dir = _project(tmp_path)
    run = prepare_run(config, "openai", sample_size=1, seed=3)
    attach_files_to_run(run, column="attachment_file", files_dir=files_dir, acknowledge_unestimated_cost=True)

    class AttachedSyncAdapter(FakeAdapter):
        def __init__(self):
            super().__init__()
            self.seen = []

        def run_sync(self, payload):
            self.seen.append(payload)
            prompt = payload["body"]["input"][0]["content"][1]["text"]
            records = json.loads(prompt.split("Classify every record below:", 1)[1].strip())
            result = {
                "results": [
                    {"record_id": record["record_id"], "primary_label": "other", "secondary_label": None,
                     "confidence": 0.8, "justification": "Synthetic test result."}
                    for record in records
                ]
            }
            return NormalizedResult(custom_id=payload["custom_id"], status="succeeded", response_text=json.dumps(result), model="fake-model", input_tokens=100, output_tokens=20)

    fake = AttachedSyncAdapter()
    submit_run(run, fake)
    assert len(fake.seen) == 1
    assert isinstance(fake.seen[0]["body"]["input"], list)
    summary = json.loads((run / "run_reports" / "run_summary.json").read_text(encoding="utf-8"))
    assert summary["estimated_maximum_usd"] is None
    assert summary["text_only_estimate_usd"] == _manifest(run)["cost_estimate"]["estimated_usd"]
    assert summary["cost_estimate_scope"] == "text_only_excludes_files"
    estimate = extrapolate_cost_from_run(run)
    assert estimate["has_file_attachments"] is True
    assert any("cannot be separated" in item for item in estimate["limitations"])
    assert "excludes_file_input_cost" not in estimate


@pytest.mark.parametrize("filename", ["", "missing.pdf", "../outside.pdf", "/tmp/outside.pdf", "bad.txt"])
def test_invalid_attachment_fails_without_mutating_run(tmp_path, filename):
    config, files_dir = _project(tmp_path)
    frame = pd.read_csv(config.parent / "data" / "input-data.csv", dtype=str).fillna("")
    frame.loc[0, "attachment_file"] = filename
    frame.to_csv(config.parent / "data" / "input-data.csv", index=False)
    run = prepare_run(config, "openai", execution="batch")
    manifest_before = (run / "manifest.json").read_bytes()
    with pytest.raises(ValueError):
        attach_files_to_run(run, column="attachment_file", files_dir=files_dir, acknowledge_unestimated_cost=True)
    assert (run / "manifest.json").read_bytes() == manifest_before
    assert "file_attachments" not in _manifest(run)


def test_explicit_cost_acknowledgment_and_no_double_attachment(tmp_path):
    config, files_dir = _project(tmp_path)
    run = prepare_run(config, "openai")
    with pytest.raises(ValueError, match="acknowledge-unestimated-cost"):
        attach_files_to_run(run, column="attachment_file", files_dir=files_dir, acknowledge_unestimated_cost=False)
    attach_files_to_run(run, column="attachment_file", files_dir=files_dir, acknowledge_unestimated_cost=True)
    with pytest.raises(ValueError, match="already attached"):
        attach_files_to_run(run, column="attachment_file", files_dir=files_dir, acknowledge_unestimated_cost=True)


def test_cli_attachment_and_submission_warn_about_unknown_cost(tmp_path, monkeypatch):
    config, files_dir = _project(tmp_path)
    run = prepare_run(config, "openai", execution="batch")
    runner = CliRunner()
    result = runner.invoke(app, ["attach-files", str(run), "--column", "attachment_file", "--files-dir", str(files_dir), "--acknowledge-unestimated-cost"])
    assert result.exit_code == 0, result.stdout
    assert "Total cost is unknown" in result.stdout
    fake = FakeAdapter()
    monkeypatch.setattr(core, "get_provider", lambda name: fake)
    result = runner.invoke(app, ["submit", str(run), "--yes"])
    assert result.exit_code == 0, result.stdout
    assert "File input cost is unknown" in result.stdout
    assert "estimate covers text only" in result.stdout
    assert fake.submissions == 1


def test_anthropic_image_size_limit_is_checked_locally(tmp_path, monkeypatch):
    config, files_dir = _project(tmp_path)
    run = prepare_run(config, "anthropic")
    manifest_before = (run / "manifest.json").read_bytes()
    monkeypatch.setattr(attachment_module, "MAX_ANTHROPIC_IMAGE_BASE64_BYTES", 20)
    with pytest.raises(ValueError, match="encoded-image limit"):
        attach_files_to_run(run, column="attachment_file", files_dir=files_dir, acknowledge_unestimated_cost=True)
    assert (run / "manifest.json").read_bytes() == manifest_before


def test_symlink_outside_file_directory_is_rejected(tmp_path):
    config, files_dir = _project(tmp_path)
    (files_dir / "first.png").unlink()
    outside = tmp_path / "outside.png"
    outside.write_bytes(PNG_BYTES)
    (files_dir / "first.png").symlink_to(outside)
    run = prepare_run(config, "openai")
    with pytest.raises(ValueError, match="outside"):
        attach_files_to_run(run, column="attachment_file", files_dir=files_dir, acknowledge_unestimated_cost=True)


def test_modified_attached_request_is_not_submitted(tmp_path):
    config, files_dir = _project(tmp_path)
    run = prepare_run(config, "openai")
    attach_files_to_run(run, column="attachment_file", files_dir=files_dir, acknowledge_unestimated_cost=True)
    attached = run / _manifest(run)["file_attachments"]["request_files"]["0"]
    attached.write_bytes(attached.read_bytes() + b"\n")
    fake = FakeAdapter()
    with pytest.raises(ValueError, match="Prepared artifact changed"):
        submit_run(run, fake)
    assert fake.submissions == 0


def test_attached_retry_requires_reattachment(tmp_path):
    config, files_dir = _project(tmp_path)
    run = prepare_run(config, "openai", execution="batch")
    attach_files_to_run(run, column="attachment_file", files_dir=files_dir, acknowledge_unestimated_cost=True)
    outputs = run / "outputs"
    outputs.mkdir()
    (outputs / "failures.jsonl").write_text(json.dumps({"record_id": "GRANT-001", "category": "errored"}) + "\n", encoding="utf-8")
    child = prepare_retry(run)
    assert _manifest(child)["file_attachments"]["status"] == "required"
    fake = FakeAdapter()
    with pytest.raises(ValueError, match="needs files attached"):
        submit_run(child, fake)
    assert fake.submissions == 0
    attach_files_to_run(child, column="attachment_file", files_dir=files_dir, acknowledge_unestimated_cost=True)
    assert _manifest(child)["file_attachments"]["status"] == "ready"
    submit_run(child, fake)
    assert fake.submissions == 1


def test_retry_rejects_file_changed_since_parent(tmp_path):
    config, files_dir = _project(tmp_path)
    run = prepare_run(config, "openai")
    attach_files_to_run(run, column="attachment_file", files_dir=files_dir, acknowledge_unestimated_cost=True)
    outputs = run / "outputs"
    outputs.mkdir()
    (outputs / "failures.jsonl").write_text(json.dumps({"record_id": "GRANT-001", "category": "errored"}) + "\n", encoding="utf-8")
    child = prepare_retry(run)
    (files_dir / "first.png").write_bytes(PNG_BYTES + b"changed")
    with pytest.raises(ValueError, match="changed since the parent run"):
        attach_files_to_run(child, column="attachment_file", files_dir=files_dir, acknowledge_unestimated_cost=True)
    assert _manifest(child)["file_attachments"]["status"] == "required"
