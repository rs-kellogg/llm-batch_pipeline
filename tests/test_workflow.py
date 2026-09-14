from __future__ import annotations

import json
import shutil

import pandas as pd
import pytest
import yaml

import kellogg_llm_batch.core as core
from kellogg_llm_batch.core import audit_run, compare_runs, generate_pilot, pilot_project, prepare_retry, prepare_run, run_pilot, submit_run, sync_run
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
    assert len(list((run / "requests").glob("segment_*.jsonl"))) == 1
    submit_run(run, fake)
    submit_run(run, fake)
    assert fake.submissions == 1
    sync_run(run, adapter=fake)
    results = pd.read_parquet(run / "results" / "results.parquet")
    assert len(results) == 10
    assert results["record_id"].is_unique
    assert audit_run(run)["complete"] is True
    provenance = json.loads((run / "results" / "provenance.json").read_text())
    assert len(provenance["rows"]) == 10
    assert provenance["rows"][0]["source_row_sha256"]
    assert provenance["rows"][0]["config_sha256"]
    assert provenance["rows"][0]["validation_status"] == "valid"
    assert load_state(run)["stage"] == "audited"


def test_compare_exports_disagreements(example_config, tmp_path, monkeypatch):
    fake = FakeAdapter()
    monkeypatch.setattr(core, "get_provider", lambda name: fake)
    config = _temporary_config(example_config, tmp_path)
    run_a = prepare_run(config, "openai")
    run_b = prepare_run(config, "openai")
    for run in (run_a, run_b):
        submit_run(run, fake)
        sync_run(run, adapter=fake)
    changed = pd.read_parquet(run_b / "results" / "results.parquet")
    changed.loc[0, "primary_label"] = "technical"
    changed.to_parquet(run_b / "results" / "results.parquet", index=False)
    report = compare_runs(run_a, run_b)
    assert report["disagreement_rows"] >= 1
    assert report["metrics"]["primary_label"]["percent_agreement"] < 1


def test_pilot_validates_sample_output(example_config, tmp_path, monkeypatch):
    fake = FakeAdapter()
    monkeypatch.setattr(core, "get_provider", lambda name: fake)
    config = _temporary_config(example_config, tmp_path)
    report = pilot_project(config, "openai", fake)
    assert report["valid"] is True
    assert report["sample_records"] == 4
    assert report["valid_records"] == 4
    assert report["actual_pilot_usage"]["input_tokens"] > 0


def test_pilot_generate_then_run_exact_saved_requests(example_config, tmp_path, monkeypatch):
    fake = FakeAdapter()
    monkeypatch.setattr(core, "get_provider", lambda name: fake)
    config = _temporary_config(example_config, tmp_path)
    pilot = generate_pilot(config, "openai")

    assert not (pilot / "results.json").exists()
    assert (pilot / "sampled_source.csv").exists()
    model_records = [json.loads(line) for line in (pilot / "sampled_records.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(model_records) == 4
    assert set(model_records[0]) == {"record_id", "title", "text"}
    rendered = [json.loads(line) for line in (pilot / "rendered_prompts.jsonl").read_text(encoding="utf-8").splitlines()]
    assert '"record_id"' in rendered[0]["user_prompt"]
    assert '"title"' in rendered[0]["user_prompt"]
    assert "investigator" not in rendered[0]["user_prompt"]

    report = run_pilot(pilot, fake)
    assert report["valid"] is True
    assert report["sample_records"] == 4
    assert (pilot / "predictions.parquet").exists()
    with pytest.raises(ValueError, match="already has results"):
        run_pilot(pilot, fake)
    requests_path = pilot / "provider_requests.jsonl"
    requests_path.write_text(requests_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="artifact changed"):
        run_pilot(pilot, fake, rerun=True)


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
    failures = pd.read_parquet(run / "results" / "failures.parquet")
    assert len(set(failures["record_id"])) == 1
    child = prepare_retry(run)
    child_input = pd.read_parquet(child / "requests" / "canonical_input.parquet")
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
