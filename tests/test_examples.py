from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pandas as pd
import pytest

import kellogg_llm_batch.core as core
from kellogg_llm_batch.attachments import attach_files_to_run
from kellogg_llm_batch.core import merge_run, prepare_retry, prepare_run, submit_run, sync_run
from kellogg_llm_batch.models import NormalizedResult
from kellogg_llm_batch.providers.base import ProviderAdapter
from kellogg_llm_batch.validation import validate_project
from conftest import FakeAdapter


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CONTROLLED_RETRY_IDS = {"MRETRY-002", "MRETRY-014", "MRETRY-026"}


def _copy_example(tmp_path: Path, name: str) -> Path:
    destination = tmp_path / name
    shutil.copytree(
        REPOSITORY_ROOT / "examples" / name,
        destination,
        ignore=shutil.ignore_patterns("runs"),
    )
    return destination


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_multisegment_example_prepares_five_segments(tmp_path, provider):
    example = _copy_example(tmp_path, "grant_coding")
    config = example / "project.multisegment.yaml"

    report = validate_project(config)
    assert report["valid"] is True
    assert report["source_rows"] == 24
    assert report["request_count"] == 24
    assert report["segment_estimates"][provider]["segments"] == 5
    assert report["segment_estimates"][provider]["max_requests_per_batch"] == 5

    run = prepare_run(config, provider, execution="batch")
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    request_counts = [
        len(ProviderAdapter.read_jsonl(path))
        for path in sorted((run / "api_requests").glob("segment_*.jsonl"))
    ]
    assert manifest["request_count"] == 24
    assert manifest["segment_count"] == 5
    assert request_counts == [5, 5, 5, 5, 4]
    if provider == "anthropic":
        assert manifest["provider_options"] == {}
        payloads = [
            payload
            for path in sorted((run / "api_requests").glob("segment_*.jsonl"))
            for payload in ProviderAdapter.read_jsonl(path)
        ]
        assert all("temperature" not in payload["params"] for payload in payloads)


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_multisegment_retry_example_prepares_controlled_segments(tmp_path, provider):
    example = _copy_example(tmp_path, "grant_coding") / "multisegment-retry"
    config = example / "project.yaml"

    report = validate_project(config)
    assert report["valid"] is True
    assert report["source_rows"] == 30
    assert report["request_count"] == 10
    assert report["segment_estimates"][provider]["segments"] == 5
    assert report["segment_estimates"][provider]["max_requests_per_batch"] == 2

    run = prepare_run(config, provider, execution="batch")
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    request_counts = [
        len(ProviderAdapter.read_jsonl(path))
        for path in sorted((run / "api_requests").glob("segment_*.jsonl"))
    ]
    request_map = ProviderAdapter.read_jsonl(
        run / "input_snapshot" / "request_map.jsonl"
    )
    source = pd.read_csv(config.parent / "data" / "input-data.csv", keep_default_na=False)
    controlled = source.loc[
        source["retry_case"] == "omit_when_trigger_present",
        ["grant_id", "retry_trigger_id"],
    ]
    trigger_by_record = dict(
        zip(controlled["grant_id"], controlled["retry_trigger_id"], strict=True)
    )
    segment_by_record = {
        record_id: item["segment_index"]
        for item in request_map
        for record_id in item["record_ids"]
    }
    request_records_by_id = {
        record_id: set(item["record_ids"])
        for item in request_map
        for record_id in item["record_ids"]
    }

    assert manifest["request_count"] == 10
    assert manifest["segment_count"] == 5
    assert request_counts == [2, 2, 2, 2, 2]
    assert {
        record_id: segment_by_record[record_id]
        for record_id in sorted(CONTROLLED_RETRY_IDS)
    } == {"MRETRY-002": 0, "MRETRY-014": 2, "MRETRY-026": 4}
    assert trigger_by_record == {
        "MRETRY-002": "MRETRY-001",
        "MRETRY-014": "MRETRY-013",
        "MRETRY-026": "MRETRY-025",
    }
    assert all(
        trigger_id in request_records_by_id[record_id]
        for record_id, trigger_id in trigger_by_record.items()
    )
    assert (example / "schema.json").read_bytes() == (
        example.parent / "schema.json"
    ).read_bytes()
    assert (example / "context" / "codebook.csv").read_bytes() == (
        example.parent / "context" / "codebook.csv"
    ).read_bytes()


class ControlledRetryAdapter(FakeAdapter):
    prompt_marker = "Classify every record that remains after applying that rule:"

    def download(self, batch_id, output_path, error_path):
        with output_path.open("w", encoding="utf-8") as handle:
            for payload in self.batches[batch_id]:
                records = json.loads(
                    payload["body"]["input"].split(self.prompt_marker, 1)[1].strip()
                )
                request_ids = {record["record_id"] for record in records}
                included = [
                    record
                    for record in records
                    if not (
                        record["retry_case"] == "omit_when_trigger_present"
                        and record["retry_trigger_id"] in request_ids
                    )
                ]
                results = [
                    {
                        "record_id": record["record_id"],
                        "primary_label": "other",
                        "secondary_label": None,
                        "confidence": 0.8,
                        "justification": "Synthetic controlled-retry result.",
                    }
                    for record in included
                ]
                handle.write(
                    json.dumps(
                        {
                            "custom_id": payload["custom_id"],
                            "status": "succeeded",
                            "response_text": json.dumps({"results": results}),
                        }
                    )
                    + "\n"
                )

    def normalize_line(self, obj):
        return NormalizedResult(
            custom_id=obj["custom_id"],
            status=obj["status"],
            response_text=obj.get("response_text"),
            model="fake-controlled-retry",
            input_tokens=100,
            output_tokens=20,
        )


def test_multisegment_retry_complete_parent_child_merge(tmp_path, monkeypatch):
    example = _copy_example(tmp_path, "grant_coding") / "multisegment-retry"
    adapter = ControlledRetryAdapter()
    monkeypatch.setattr(core, "get_provider", lambda name: adapter)

    parent = prepare_run(example / "project.yaml", "openai", execution="batch")
    submit_run(parent, adapter)
    sync_run(parent, adapter=adapter)

    failures = ProviderAdapter.read_jsonl(parent / "outputs" / "failures.jsonl")
    parent_audit = json.loads(
        (parent / "run_reports" / "audit.json").read_text(encoding="utf-8")
    )
    assert {item["record_id"] for item in failures} == CONTROLLED_RETRY_IDS
    assert {item["category"] for item in failures} == {"missing_output"}
    assert parent_audit["valid_records"] == 27
    assert set(parent_audit["missing_record_ids"]) == CONTROLLED_RETRY_IDS
    assert parent_audit["complete"] is False

    child = prepare_retry(parent)
    child_input = pd.read_parquet(
        child / "input_snapshot" / "canonical_input.parquet"
    )
    child_manifest = json.loads(
        (child / "manifest.json").read_text(encoding="utf-8")
    )
    assert child_input["record_id"].astype(str).tolist() == sorted(
        CONTROLLED_RETRY_IDS
    )
    assert child_manifest["purpose"] == "retry"
    assert child_manifest["parent_run"] == str(parent)
    assert child_manifest["request_count"] == 1
    assert child_manifest["segment_count"] == 1

    submit_run(child, adapter)
    sync_run(child, adapter=adapter)
    child_audit = json.loads(
        (child / "run_reports" / "audit.json").read_text(encoding="utf-8")
    )
    assert child_audit["valid_records"] == 3
    assert child_audit["complete"] is True
    assert not (child / "outputs" / "failures.jsonl").exists()

    merged_path = merge_run(child)
    merged = pd.read_parquet(merged_path)
    expected_ids = {f"MRETRY-{index:03d}" for index in range(1, 31)}
    assert len(merged) == 30
    assert merged["record_id"].astype(str).nunique() == 30
    assert set(merged["record_id"].astype(str)) == expected_ids
    assert (child / "outputs" / "merged.csv").is_file()


def _expected_attachment_hashes(example: Path) -> dict[str, str]:
    inventory = example / "data" / "attachments" / "SHA256SUMS"
    return {
        relative: digest
        for digest, relative in (
            line.split(maxsplit=1)
            for line in inventory.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    }


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_job_post_example_builds_checked_attached_payloads(tmp_path, provider):
    example = _copy_example(tmp_path, "job_post_attachments")
    config = example / "project.yaml"
    attachments = example / "data" / "attachments"
    expected_hashes = _expected_attachment_hashes(example)

    assert len(expected_hashes) == 4
    for relative, expected in expected_hashes.items():
        data = (attachments / relative).read_bytes()
        assert hashlib.sha256(data).hexdigest() == expected
        if relative.endswith(".pdf"):
            assert data.startswith(b"%PDF-")
        else:
            assert data.startswith(b"\x89PNG\r\n\x1a\n")

    report = validate_project(config)
    assert report["valid"] is True
    assert report["source_rows"] == 4
    assert report["request_count"] == 4
    assert report["segment_estimates"][provider]["segments"] == 2

    run = prepare_run(config, provider, execution="batch")
    originals = {
        path.name: path.read_bytes()
        for path in sorted((run / "api_requests").glob("segment_*.jsonl"))
    }
    if provider == "anthropic":
        original_payloads = [
            payload
            for path in sorted((run / "api_requests").glob("segment_*.jsonl"))
            for payload in ProviderAdapter.read_jsonl(path)
        ]
        assert all("temperature" not in payload["params"] for payload in original_payloads)
    attach_files_to_run(
        run,
        column="attachment_file",
        files_dir=attachments,
        acknowledge_unestimated_cost=True,
    )

    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["segment_count"] == 2
    assert len(manifest["file_attachments"]["files"]) == 4
    assert {
        item["media_type"] for item in manifest["file_attachments"]["files"]
    } == {"application/pdf", "image/png"}
    assert {
        item["path"]: item["sha256"]
        for item in manifest["file_attachments"]["files"]
    } == expected_hashes
    assert originals == {
        path.name: path.read_bytes()
        for path in sorted((run / "api_requests").glob("segment_*.jsonl"))
    }

    attached_payloads = []
    for relative in manifest["file_attachments"]["request_files"].values():
        attached_payloads.extend(ProviderAdapter.read_jsonl(run / relative))
    assert len(attached_payloads) == 4
    if provider == "openai":
        file_part_types = {
            payload["body"]["input"][0]["content"][0]["type"]
            for payload in attached_payloads
        }
        assert file_part_types == {"input_file", "input_image"}
    else:
        file_part_types = {
            payload["params"]["messages"][0]["content"][0]["type"]
            for payload in attached_payloads
        }
        assert file_part_types == {"document", "image"}


def test_example_shell_scripts_are_valid_bash():
    scripts = sorted(
        (REPOSITORY_ROOT / "examples" / "grant_coding" / "multisegment").glob("*.sh")
    ) + sorted(
        (REPOSITORY_ROOT / "examples" / "grant_coding" / "multisegment-retry").glob("*.sh")
    ) + sorted(
        (REPOSITORY_ROOT / "examples" / "grant_coding" / "walkthrough").glob("*.sh")
    ) + sorted(
        (REPOSITORY_ROOT / "examples" / "job_post_attachments").glob("*.sh")
    )
    assert len(scripts) == 25
    for script in scripts:
        subprocess.run(["bash", "-n", str(script)], check=True)
