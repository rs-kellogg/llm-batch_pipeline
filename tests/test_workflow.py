from __future__ import annotations

import json
import shutil

import pandas as pd
import pytest
import yaml

import kellogg_llm_batch.core as core
from kellogg_llm_batch.core import audit_run, compare_runs, prepare_retry, prepare_run, submit_run, sync_run
from kellogg_llm_batch.state import load_state, save_state

from conftest import FakeAdapter


def _temporary_config(example_config, tmp_path):
    raw = yaml.safe_load(example_config.read_text())
    raw["input"]["path"] = str(example_config.parent / "data" / "grants.csv")
    raw["task"]["output_schema"] = str(example_config.parent / "schema.json")
    raw["prompt"]["system_file"] = str(example_config.parent / "prompts" / "system.txt")
    raw["prompt"]["user_file"] = str(example_config.parent / "prompts" / "user.txt")
    raw["prompt"]["context"]["codebook"]["path"] = str(example_config.parent / "context" / "codebook.csv")
    raw["output"]["runs_directory"] = str(tmp_path / "runs")
    path = tmp_path / "project.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return path


def test_prepare_submit_sync_and_audit(example_config, tmp_path, monkeypatch):
    fake = FakeAdapter()
    monkeypatch.setattr(core, "get_provider", lambda name: fake)
    config = _temporary_config(example_config, tmp_path)
    run = prepare_run(config, "openai")
    assert (run / "manifest.json").exists()
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["layout_version"] == 2
    assert manifest["purpose"] == "production"
    assert manifest["execution"] == "batch"
    assert manifest["cost_estimate"]["execution"] == "batch"
    assert {path.name for path in run.iterdir()} == {
        "REVIEW.md",
        "input_snapshot",
        "manifest.json",
        "api_requests",
        "project_snapshot",
        "state.json",
    }
    assert {path.name for path in (run / "input_snapshot").iterdir()} == {
        "canonical_input.parquet",
        "request_map.jsonl",
    }
    assert not (run / "api_requests" / "canonical_input.csv").exists()
    assert not (run / "api_requests" / "model_records.jsonl").exists()
    assert not (run / "api_requests" / "rendered_prompts.jsonl").exists()
    review = (run / "REVIEW.md").read_text(encoding="utf-8")
    assert "What to inspect" in review
    assert "body.instructions" in review
    assert "kllm-batch submit ." in review
    assert "/Users/" not in review
    assert len(list((run / "api_requests").glob("segment_*.jsonl"))) == 1
    submit_run(run, fake)
    submit_run(run, fake)
    assert fake.submissions == 1
    sync_run(run, adapter=fake)
    results = pd.read_parquet(run / "outputs" / "results.parquet")
    assert len(results) == 10
    assert results["record_id"].is_unique
    assert (run / "outputs" / "results.csv").exists()
    assert not (run / "outputs" / "failures.jsonl").exists()
    assert not (run / "outputs" / "failures.parquet").exists()
    assert not (run / "outputs" / "failures.csv").exists()
    assert not (run / "outputs" / "provenance.json").exists()
    for column in (
        "run_id",
        "provider",
        "model_requested",
        "model_returned",
        "prompt_version",
        "custom_id",
        "batch_id",
        "validation_status",
        "input_tokens_request",
        "output_tokens_request",
        "actual_request_cost_usd",
    ):
        assert column in results
    assert set(results["validation_status"]) == {"valid"}
    assert results["_kllm_source_row_sha256"].notna().all()
    assert audit_run(run)["complete"] is True
    assert load_state(run)["stage"] == "audited"


def test_consolidated_request_map_supports_multiple_segments(example_config, tmp_path, monkeypatch):
    fake = FakeAdapter()
    monkeypatch.setattr(core, "get_provider", lambda name: fake)
    config = _temporary_config(example_config, tmp_path)
    raw = yaml.safe_load(config.read_text(encoding="utf-8"))
    raw["providers"]["openai"]["max_requests_per_batch"] = 2
    config.write_text(yaml.safe_dump(raw), encoding="utf-8")

    run = prepare_run(config, "openai")
    assert len(list((run / "api_requests").glob("segment_*.jsonl"))) == 2
    assert [path.name for path in (run / "input_snapshot").glob("*map*")] == ["request_map.jsonl"]
    submit_run(run, fake)
    sync_run(run, adapter=fake)

    results = pd.read_parquet(run / "outputs" / "results.parquet")
    assert len(results) == 10
    assert results["record_id"].is_unique


def test_compare_exports_disagreements(example_config, tmp_path, monkeypatch):
    fake = FakeAdapter()
    monkeypatch.setattr(core, "get_provider", lambda name: fake)
    config = _temporary_config(example_config, tmp_path)
    run_a = prepare_run(config, "openai")
    run_b = prepare_run(config, "openai")
    for run in (run_a, run_b):
        submit_run(run, fake)
        sync_run(run, adapter=fake)
    changed = pd.read_parquet(run_b / "outputs" / "results.parquet")
    changed.loc[0, "primary_label"] = "technical"
    changed.to_parquet(run_b / "outputs" / "results.parquet", index=False)
    report = compare_runs(run_a, run_b)
    assert report["disagreement_rows"] >= 1
    assert report["metrics"]["primary_label"]["percent_agreement"] < 1


def test_prepare_sample_then_submit_sync_uses_exact_saved_requests(example_config, tmp_path, monkeypatch):
    fake = FakeAdapter()
    monkeypatch.setattr(core, "get_provider", lambda name: fake)
    config = _temporary_config(example_config, tmp_path)
    raw = yaml.safe_load(config.read_text(encoding="utf-8"))
    raw["input"]["fields_sent"] = {
        "research_heading": "project_title",
        "document_body": "abstract",
        "award_year_seen_by_model": "year",
    }
    raw["input"]["required_fields"] = ["document_body"]
    raw["input"]["field_limits"] = {}
    config.write_text(yaml.safe_dump(raw), encoding="utf-8")
    run = prepare_run(config, "openai", sample_size=4, seed=17)

    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["purpose"] == "pilot"
    assert manifest["execution"] == "sync"
    assert manifest["cost_estimate"]["execution"] == "sync"
    assert manifest["selection"] == {"method": "random", "selected_count": 4, "seed": 17}
    assert manifest["selected_rows"] == 4
    payloads = [json.loads(line) for line in (run / "api_requests" / "segment_0000.jsonl").read_text(encoding="utf-8").splitlines()]
    assert '"record_id"' in payloads[0]["body"]["input"]
    for logical_name in raw["input"]["fields_sent"]:
        assert f'"{logical_name}"' in payloads[0]["body"]["input"]
    assert "investigator" not in payloads[0]["body"]["input"]

    state = submit_run(run, fake)
    assert state["status"] == "completed"
    assert state["stage"] == "audited"
    assert len(pd.read_parquet(run / "outputs" / "results.parquet")) == 4
    sync_calls = fake.sync_calls
    submit_run(run, fake)
    assert fake.sync_calls == sync_calls


def test_prepare_with_explicit_ids_and_request_integrity(example_config, tmp_path, monkeypatch):
    fake = FakeAdapter()
    monkeypatch.setattr(core, "get_provider", lambda name: fake)
    config = _temporary_config(example_config, tmp_path)
    ids_file = tmp_path / "pilot_ids.txt"
    ids_file.write_text("GRANT-007\nGRANT-002\n", encoding="utf-8")
    run = prepare_run(config, "openai", ids_file=ids_file)
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["selection"]["method"] == "ids_file"
    assert manifest["execution"] == "sync"
    selected = pd.read_parquet(run / "input_snapshot" / "canonical_input.parquet")
    assert selected["record_id"].tolist() == ["GRANT-002", "GRANT-007"]

    requests = run / "api_requests" / "segment_0000.jsonl"
    requests.write_text(requests.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="artifact changed"):
        submit_run(run, fake)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"sample_size": 2, "ids_file": "unused.txt"}, "only one record selection"),
        ({"seed": 7}, "seed can only be used"),
        ({"sample_size": 11}, "exceeds the 10 available records"),
    ],
)
def test_prepare_rejects_invalid_selection_options(example_config, tmp_path, monkeypatch, kwargs, message):
    monkeypatch.setattr(core, "get_provider", lambda name: FakeAdapter())
    config = _temporary_config(example_config, tmp_path)
    with pytest.raises(ValueError, match=message):
        prepare_run(config, "openai", **kwargs)
    assert not (tmp_path / "runs").exists()


def test_prepare_rejects_duplicate_and_unknown_explicit_ids(example_config, tmp_path, monkeypatch):
    monkeypatch.setattr(core, "get_provider", lambda name: FakeAdapter())
    config = _temporary_config(example_config, tmp_path)
    ids_file = tmp_path / "pilot_ids.txt"
    ids_file.write_text("GRANT-002\nGRANT-002\n", encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate IDs"):
        prepare_run(config, "openai", ids_file=ids_file)
    assert not (tmp_path / "runs").exists()

    ids_file.write_text("GRANT-002\nNOT-IN-SOURCE\n", encoding="utf-8")
    with pytest.raises(ValueError, match="do not exist in source"):
        prepare_run(config, "openai", ids_file=ids_file)
    assert not (tmp_path / "runs").exists()


@pytest.mark.parametrize(
    ("kwargs", "purpose", "execution"),
    [
        ({"sample_size": 2, "execution": "batch"}, "pilot", "batch"),
        ({"execution": "sync"}, "production", "sync"),
    ],
)
def test_prepare_allows_explicit_execution_override(example_config, tmp_path, monkeypatch, kwargs, purpose, execution):
    monkeypatch.setattr(core, "get_provider", lambda name: FakeAdapter())
    config = _temporary_config(example_config, tmp_path)
    run = prepare_run(config, "openai", **kwargs)
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["purpose"] == purpose
    assert manifest["execution"] == execution
    assert manifest["cost_estimate"]["execution"] == execution


class IncompleteAdapter(FakeAdapter):
    def download(self, batch_id, output_path, error_path):
        with output_path.open("w", encoding="utf-8") as handle:
            for index, payload in enumerate(self.batches[batch_id]):
                records = json.loads(payload["body"]["input"].split("Classify every record below:", 1)[1].strip())
                if index == 0:
                    records = records[:-1]
                results = [{"record_id": record["record_id"], "primary_label": "other", "secondary_label": None, "confidence": 0.8, "justification": "Synthetic test result."} for record in records]
                handle.write(json.dumps({"custom_id": payload["custom_id"], "status": "succeeded", "response_text": json.dumps({"results": results})}) + "\n")


def test_manual_retry_contains_only_failed_rows(example_config, tmp_path, monkeypatch):
    fake = IncompleteAdapter()
    monkeypatch.setattr(core, "get_provider", lambda name: fake)
    config = _temporary_config(example_config, tmp_path)
    run = prepare_run(config, "openai")
    submit_run(run, fake)
    sync_run(run, adapter=fake)
    failures = pd.DataFrame(
        json.loads(line)
        for line in (run / "outputs" / "failures.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    )
    assert len(set(failures["record_id"])) == 1
    assert set(failures["validation_status"]) == {"invalid"}
    assert failures["_kllm_source_row_sha256"].notna().all()
    assert not (run / "outputs" / "failures.parquet").exists()
    assert not (run / "outputs" / "failures.csv").exists()
    child = prepare_retry(run)
    child_input = pd.read_parquet(child / "input_snapshot" / "canonical_input.parquet")
    assert len(child_input) == 1
    assert json.loads((child / "manifest.json").read_text())["parent_run"] == str(run)


def test_retry_blocks_if_source_changed(example_config, tmp_path, monkeypatch):
    fake = IncompleteAdapter()
    monkeypatch.setattr(core, "get_provider", lambda name: fake)
    source = tmp_path / "grants.csv"
    shutil.copy2(example_config.parent / "data" / "grants.csv", source)
    config = _temporary_config(example_config, tmp_path)
    raw = yaml.safe_load(config.read_text())
    raw["input"]["path"] = str(source)
    config.write_text(yaml.safe_dump(raw), encoding="utf-8")
    run = prepare_run(config, "openai")
    submit_run(run, fake)
    sync_run(run, adapter=fake)
    source.write_text(source.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Source data changed"):
        prepare_retry(run)


def test_state_recovers_last_valid_copy(example_config, tmp_path, monkeypatch):
    monkeypatch.setattr(core, "get_provider", lambda name: FakeAdapter())
    run = prepare_run(_temporary_config(example_config, tmp_path), "openai")
    original = load_state(run)
    save_state(run, {**original, "status": "submitted", "stage": "submitted"})
    (run / "state.json").write_text("{broken", encoding="utf-8")
    recovered = load_state(run)
    assert recovered["status"] == "prepared"
    assert recovered["stage"] == "prepared"
