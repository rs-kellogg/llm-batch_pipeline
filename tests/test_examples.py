from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from kellogg_llm_batch.attachments import attach_files_to_run
from kellogg_llm_batch.core import prepare_run
from kellogg_llm_batch.providers.base import ProviderAdapter
from kellogg_llm_batch.validation import validate_project


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


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
        (REPOSITORY_ROOT / "examples" / "grant_coding" / "walkthrough").glob("*.sh")
    ) + sorted(
        (REPOSITORY_ROOT / "examples" / "job_post_attachments").glob("*.sh")
    )
    assert len(scripts) == 18
    for script in scripts:
        subprocess.run(["bash", "-n", str(script)], check=True)
