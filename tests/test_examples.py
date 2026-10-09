from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

import kellogg_llm_batch.core as core
from kellogg_llm_batch.attachments import attach_files_to_run
from kellogg_llm_batch.core import merge_run, prepare_retry, prepare_run, submit_run, sync_run
from kellogg_llm_batch.providers.base import ProviderAdapter
from kellogg_llm_batch.validation import validate_project
from conftest import FakeAdapter


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_RETRY_IDS = {"MRETRY-002", "MRETRY-014", "MRETRY-026"}


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
def test_multisegment_retry_example_prepares_normal_segments(tmp_path, provider):
    example = _copy_example(tmp_path, "grant_coding_retry")
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
    payload_text = "\n".join(
        json.dumps(payload)
        for path in sorted((run / "api_requests").glob("segment_*.jsonl"))
        for payload in ProviderAdapter.read_jsonl(path)
    )

    assert manifest["request_count"] == 10
    assert manifest["segment_count"] == 5
    assert request_counts == [2, 2, 2, 2, 2]
    assert len(request_map) == 10
    assert "retry_case" not in payload_text
    assert "retry_trigger_id" not in payload_text
    assert "omit_when_trigger_present" not in payload_text
    standard = REPOSITORY_ROOT / "examples" / "grant_coding"
    assert (example / "schema.json").read_bytes() == (standard / "schema.json").read_bytes()
    assert (example / "context" / "codebook.csv").read_bytes() == (
        standard / "context" / "codebook.csv"
    ).read_bytes()


def _materialize_retry_fixture(example: Path, provider: str) -> Path:
    python_bin = str(Path(sys.executable).parent)
    environment = {**os.environ, "PATH": python_bin + os.pathsep + os.environ["PATH"]}
    subprocess.run(
        ["bash", str(example / "setup-parent.sh"), provider],
        check=True,
        cwd=example.parent,
        env=environment,
        capture_output=True,
        text=True,
    )
    parents = sorted((example / "runs").glob(f"*_{provider}_*"))
    assert len(parents) == 1
    return parents[0]


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_multisegment_retry_fixture_is_portable_and_prepares_child(
    tmp_path, provider
):
    example = _copy_example(tmp_path, "grant_coding_retry")
    fixture_root = example / "fixtures"
    inventory = fixture_root / "SHA256SUMS"
    entries = [
        line.split(maxsplit=1)
        for line in inventory.read_text(encoding="utf-8").splitlines()
        if line.strip() and line.split(maxsplit=1)[1].startswith(f"{provider}/")
    ]
    fixture_files = {
        path.relative_to(fixture_root).as_posix()
        for path in (fixture_root / provider).rglob("*")
        if path.is_file()
    }
    assert len(entries) == 28
    assert {relative for _, relative in entries} == fixture_files
    for expected, relative in entries:
        fixture_file = fixture_root / relative
        assert hashlib.sha256(fixture_file.read_bytes()).hexdigest() == expected
        assert b"/Users/" not in fixture_file.read_bytes()

    parent = _materialize_retry_fixture(example, provider)
    manifest = json.loads((parent / "manifest.json").read_text(encoding="utf-8"))
    state = json.loads((parent / "state.json").read_text(encoding="utf-8"))
    audit = json.loads(
        (parent / "run_reports" / "audit.json").read_text(encoding="utf-8")
    )
    failures = ProviderAdapter.read_jsonl(parent / "outputs" / "failures.jsonl")

    assert manifest["provider"] == provider
    assert manifest["source_path"] == str((example / "data" / "input-data.csv").resolve())
    assert manifest["config_path"] == str((example / "project.yaml").resolve())
    assert "__EXAMPLE_ROOT__" not in (parent / "manifest.json").read_text(
        encoding="utf-8"
    )
    assert "__EXAMPLE_ROOT__" not in (
        parent / "run_reports" / "run_summary.json"
    ).read_text(encoding="utf-8")
    assert manifest["segment_count"] == 5
    assert [segment["status"] for segment in state["segments"]] == ["processed"] * 5
    assert audit["expected_records"] == 30
    assert audit["valid_records"] == 27
    assert set(audit["missing_record_ids"]) == EXPECTED_RETRY_IDS
    assert audit["complete"] is False
    assert {item["record_id"] for item in failures} == EXPECTED_RETRY_IDS
    assert {item["category"] for item in failures} == {"missing_output"}

    python_bin = str(Path(sys.executable).parent)
    environment = {**os.environ, "PATH": python_bin + os.pathsep + os.environ["PATH"]}
    repeated_setup = subprocess.run(
        ["bash", str(example / "setup-parent.sh"), provider],
        cwd=example.parent,
        env=environment,
        capture_output=True,
        text=True,
    )
    assert repeated_setup.returncode == 1
    assert "Refusing to overwrite existing run" in repeated_setup.stderr

    child = prepare_retry(parent)
    child_input = pd.read_parquet(child / "input_snapshot" / "canonical_input.parquet")
    child_manifest = json.loads((child / "manifest.json").read_text(encoding="utf-8"))
    child_payload = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((child / "api_requests").glob("segment_*.jsonl"))
    )
    assert child_input["record_id"].astype(str).tolist() == sorted(EXPECTED_RETRY_IDS)
    assert child_manifest["purpose"] == "retry"
    assert child_manifest["parent_run"] == str(parent)
    assert child_manifest["request_count"] == 1
    assert child_manifest["segment_count"] == 1
    assert "retry_case" not in child_payload
    assert "retry_trigger_id" not in child_payload
    assert "omit_when_trigger_present" not in child_payload


def test_multisegment_retry_complete_fixture_child_merge(tmp_path, monkeypatch):
    example = _copy_example(tmp_path, "grant_coding_retry")
    adapter = FakeAdapter()
    monkeypatch.setattr(core, "get_provider", lambda name: adapter)
    parent = _materialize_retry_fixture(example, "openai")
    child = prepare_retry(parent)
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
        (REPOSITORY_ROOT / "examples" / "grant_coding_retry").glob("*.sh")
    ) + sorted(
        (REPOSITORY_ROOT / "examples" / "grant_coding" / "walkthrough").glob("*.sh")
    ) + sorted(
        (REPOSITORY_ROOT / "examples" / "job_post_attachments").glob("*.sh")
    )
    assert len(scripts) == 25
    for script in scripts:
        subprocess.run(["bash", "-n", str(script)], check=True)
