from __future__ import annotations

import json
import shutil

import pandas as pd
import pytest
import yaml

import kellogg_llm_batch.core as core
from kellogg_llm_batch.core import audit_run, batch_submission_summary, cancel_run, compare_runs, prepare_retry, prepare_run, submit_run, sync_run
from kellogg_llm_batch.providers.base import ProviderAdapter
from kellogg_llm_batch.state import load_state, save_state

from conftest import FakeAdapter


def _temporary_config(example_config, tmp_path):
    raw = yaml.safe_load(example_config.read_text())
    raw["input"]["path"] = str(example_config.parent / "data" / "input-data.csv")
    raw["task"]["output_schema"] = str(example_config.parent / "schema.json")
    raw["prompt"]["system_file"] = str(example_config.parent / "prompts" / "system.txt")
    raw["prompt"]["user_file"] = str(example_config.parent / "prompts" / "user.txt")
    raw["prompt"]["context"]["codebook"]["path"] = str(example_config.parent / "context" / "codebook.csv")
    raw["output"]["runs_directory"] = str(tmp_path / "runs")
    path = tmp_path / "project.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return path


def _segmented_run(example_config, tmp_path, monkeypatch, adapter, segments=4):
    monkeypatch.setattr(core, "get_provider", lambda name: adapter)
    config = _temporary_config(example_config, tmp_path)
    raw = yaml.safe_load(config.read_text(encoding="utf-8"))
    raw["providers"]["openai"]["max_requests_per_batch"] = 1
    config.write_text(yaml.safe_dump(raw), encoding="utf-8")
    run = prepare_run(config, "openai", execution="batch")
    assert len(load_state(run)["segments"]) == segments
    return run


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
    assert manifest["gold_columns"] == {"primary_label": "reference_primary_label"}
    assert manifest["gold_labels_file"] == "input_snapshot/gold_labels.parquet"
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
        "gold_labels.parquet",
        "request_map.jsonl",
    }
    assert len(pd.read_parquet(run / manifest["gold_labels_file"])) == 10
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
    assert "reference_primary_label" not in results.columns
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
    assert {path.name for path in (run / "run_reports").iterdir()} == {
        "audit.json",
        "run_summary.json",
        "usage.json",
    }
    assert not (run / "run_reports" / "audit.md").exists()
    assert not (run / "run_reports" / "run_summary.md").exists()
    summary = json.loads((run / "run_reports" / "run_summary.json").read_text(encoding="utf-8"))
    assert summary["evaluation_metrics"]["primary_label"] == {
        "n": 10,
        "accuracy": pytest.approx(0.1),
    }
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


def test_range_submit_is_idempotent_and_open_end_sends_remainder(
    example_config, tmp_path, monkeypatch
):
    adapter = FakeAdapter()
    run = _segmented_run(example_config, tmp_path, monkeypatch, adapter)

    summary = batch_submission_summary(run, (0, 2))
    assert summary == {
        "requested_segment_range": [0, 2],
        "selected_segment_indexes": [0, 1],
        "pending_segment_indexes": [0, 1],
        "already_submitted_indexes": [],
        "request_count": 2,
    }

    state = submit_run(run, adapter, segment_range=(0, 2))
    assert adapter.submissions == 2
    assert [segment["status"] for segment in state["segments"]] == [
        "submitted",
        "submitted",
        "prepared",
        "prepared",
    ]

    submit_run(run, adapter, segment_range=(0, 2))
    assert adapter.submissions == 2
    assert batch_submission_summary(run, (0, 2))["request_count"] == 0

    state = submit_run(run, adapter, segment_range=(2, -1))
    assert adapter.submissions == 4
    assert all(segment["status"] == "submitted" for segment in state["segments"])


@pytest.mark.parametrize(
    ("segment_range", "message"),
    [
        ((-1, 1), "start must be nonnegative"),
        ((4, -1), "outside the available indexes"),
        ((0, -2), "end must be -1 or nonnegative"),
        ((2, 2), "end must be greater than start"),
        ((3, 2), "end must be greater than start"),
        ((0, 5), "exceeds the segment count"),
    ],
)
def test_submit_validates_segment_range_before_provider_calls(
    example_config, tmp_path, monkeypatch, segment_range, message
):
    adapter = FakeAdapter()
    run = _segmented_run(example_config, tmp_path, monkeypatch, adapter)

    with pytest.raises(ValueError, match=message):
        submit_run(run, adapter, segment_range=segment_range)

    assert adapter.submissions == 0
    assert all(segment["status"] == "prepared" for segment in load_state(run)["segments"])


def test_submit_without_range_skips_every_segment_with_a_remote_id(
    example_config, tmp_path, monkeypatch
):
    adapter = FakeAdapter()
    run = _segmented_run(example_config, tmp_path, monkeypatch, adapter)
    submit_run(run, adapter)

    for status in ["submitted", "running", "completed", "processed", "cancelled"]:
        state = load_state(run)
        state["segments"][0]["status"] = status
        save_state(run, state)
        submit_run(run, adapter)
        assert adapter.submissions == 4


def test_partial_sync_outputs_accumulate_and_final_audit_waits_for_all_segments(
    example_config, tmp_path, monkeypatch
):
    adapter = FakeAdapter()
    run = _segmented_run(example_config, tmp_path, monkeypatch, adapter)

    submit_run(run, adapter, segment_range=(0, 2))
    partial_state = sync_run(run, adapter=adapter)
    partial_results = pd.read_parquet(run / "outputs" / "results.parquet")

    assert [segment["status"] for segment in partial_state["segments"]] == [
        "processed",
        "processed",
        "prepared",
        "prepared",
    ]
    assert partial_state["status"] == "running"
    assert len(partial_results) == 6
    assert not (run / "run_reports" / "audit.json").exists()
    assert not (run / "run_reports" / "run_summary.json").exists()

    submit_run(run, adapter)
    final_state = sync_run(run, adapter=adapter)
    final_results = pd.read_parquet(run / "outputs" / "results.parquet")

    assert final_state["status"] == "completed"
    assert len(final_results) == 10
    assert final_results["record_id"].is_unique
    assert json.loads((run / "run_reports" / "audit.json").read_text())["complete"] is True


def test_segment_range_is_rejected_for_synchronous_runs(
    example_config, tmp_path, monkeypatch
):
    adapter = FakeAdapter()
    monkeypatch.setattr(core, "get_provider", lambda name: adapter)
    run = prepare_run(
        _temporary_config(example_config, tmp_path),
        "openai",
        sample_size=4,
        seed=42,
    )

    with pytest.raises(ValueError, match="only for batch runs"):
        submit_run(run, adapter, segment_range=(0, 1))
    with pytest.raises(ValueError, match="only for batch runs"):
        cancel_run(run, adapter, segment_range=(0, 1))

    assert adapter.submissions == 0
    assert adapter.sync_calls == 0


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

    raw_output = run / "raw_responses" / "segment_0000_output.jsonl"
    saved_lines = raw_output.read_text(encoding="utf-8").splitlines()
    raw_output.write_text(saved_lines[0] + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Run integrity error"):
        submit_run(run, fake)
    assert fake.sync_calls == sync_calls


class InterruptOnceAdapter(FakeAdapter):
    def __init__(self):
        super().__init__()
        self.calls_by_id: list[str] = []
        self.interrupted = False

    def run_sync(self, payload):
        custom_id = payload["custom_id"]
        self.calls_by_id.append(custom_id)
        if custom_id == "request_00000001" and not self.interrupted:
            self.interrupted = True
            raise KeyboardInterrupt()
        return super().run_sync(payload)


def test_sync_resume_skips_checkpointed_requests(example_config, tmp_path, monkeypatch):
    adapter = InterruptOnceAdapter()
    monkeypatch.setattr(core, "get_provider", lambda name: adapter)
    run = prepare_run(_temporary_config(example_config, tmp_path), "openai", sample_size=4, seed=17)

    with pytest.raises(KeyboardInterrupt):
        submit_run(run, adapter)
    checkpoint = run / "raw_responses" / "segment_0000_output.jsonl"
    assert len(checkpoint.read_text(encoding="utf-8").splitlines()) == 1

    state = submit_run(run, adapter)
    assert state["status"] == "completed"
    assert adapter.calls_by_id.count("request_00000000") == 1
    assert adapter.calls_by_id.count("request_00000001") == 2


def test_sync_resume_blocks_partial_checkpoint_line(example_config, tmp_path, monkeypatch):
    adapter = FakeAdapter()
    monkeypatch.setattr(core, "get_provider", lambda name: adapter)
    run = prepare_run(_temporary_config(example_config, tmp_path), "openai", sample_size=4, seed=17)
    raw_dir = run / "raw_responses"
    raw_dir.mkdir()
    (raw_dir / "segment_0000_output.jsonl").write_text('{"_kllm_normalized":', encoding="utf-8")

    with pytest.raises(ValueError, match="partial or invalid JSONL checkpoint"):
        submit_run(run, adapter)
    assert adapter.sync_calls == 0


class SyncRequestErrorAdapter(FakeAdapter):
    def run_sync(self, payload):
        if payload["custom_id"] == "request_00000000":
            raise RuntimeError("temporary provider failure")
        return super().run_sync(payload)


def test_sync_request_error_is_available_for_manual_retry(example_config, tmp_path, monkeypatch):
    adapter = SyncRequestErrorAdapter()
    monkeypatch.setattr(core, "get_provider", lambda name: adapter)
    run = prepare_run(_temporary_config(example_config, tmp_path), "openai", sample_size=4, seed=17)
    state = submit_run(run, adapter)
    assert state["status"] == "completed_with_failures"
    failures = ProviderAdapter.read_jsonl(run / "outputs" / "failures.jsonl")
    assert {row["category"] for row in failures} == {"sync_request_error"}

    child = prepare_retry(run)
    child_input = pd.read_parquet(child / "input_snapshot" / "canonical_input.parquet")
    assert set(child_input["record_id"]) == {row["record_id"] for row in failures}


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


@pytest.mark.parametrize("category", ["batch_cancelled", "canceled", "cancelled"])
def test_retry_selects_provider_cancellation_categories(
    example_config, tmp_path, monkeypatch, category
):
    fake = FakeAdapter()
    monkeypatch.setattr(core, "get_provider", lambda name: fake)
    run = prepare_run(_temporary_config(example_config, tmp_path), "openai")
    selected_id = str(
        pd.read_parquet(run / "input_snapshot" / "canonical_input.parquet").iloc[0][
            "record_id"
        ]
    )
    (run / "outputs").mkdir()
    (run / "outputs" / "failures.jsonl").write_text(
        json.dumps(
            {
                "record_id": selected_id,
                "custom_id": "request_00000000",
                "category": category,
                "message": "batch was canceled",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    child = prepare_retry(run)

    child_input = pd.read_parquet(child / "input_snapshot" / "canonical_input.parquet")
    assert child_input["record_id"].astype(str).tolist() == [selected_id]
    child_manifest = json.loads((child / "manifest.json").read_text(encoding="utf-8"))
    assert child_manifest["purpose"] == "retry"
    assert child_manifest["parent_run"] == str(run)


def test_retry_blocks_if_source_changed(example_config, tmp_path, monkeypatch):
    fake = IncompleteAdapter()
    monkeypatch.setattr(core, "get_provider", lambda name: fake)
    source = tmp_path / "grants.csv"
    shutil.copy2(example_config.parent / "data" / "input-data.csv", source)
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


def test_prepare_rejects_run_exceeding_budget(example_config, tmp_path, monkeypatch):
    monkeypatch.setattr(core, "get_provider", lambda name: FakeAdapter())
    config = _temporary_config(example_config, tmp_path)
    raw = yaml.safe_load(config.read_text(encoding="utf-8"))
    raw["budget"]["max_estimated_usd"] = 0.0000001
    config.write_text(yaml.safe_dump(raw), encoding="utf-8")

    with pytest.raises(ValueError, match=r"Estimated cost \$\d.*exceeds budget \$0\.0000"):
        prepare_run(config, "openai")
    assert not (tmp_path / "runs").exists()


def test_prepare_rejects_run_with_no_pricing_configured(example_config, tmp_path, monkeypatch):
    monkeypatch.setattr(core, "get_provider", lambda name: FakeAdapter())
    config = _temporary_config(example_config, tmp_path)
    raw = yaml.safe_load(config.read_text(encoding="utf-8"))
    raw["providers"]["openai"]["model"] = "gpt-unreleased-model"
    config.write_text(yaml.safe_dump(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="No pricing is configured for openai/gpt-unreleased-model"):
        prepare_run(config, "openai")
    assert not (tmp_path / "runs").exists()


def test_prepare_allows_unpriced_model_once_overrides_are_added(example_config, tmp_path, monkeypatch):
    monkeypatch.setattr(core, "get_provider", lambda name: FakeAdapter())
    config = _temporary_config(example_config, tmp_path)
    raw = yaml.safe_load(config.read_text(encoding="utf-8"))
    raw["providers"]["openai"]["model"] = "gpt-unreleased-model"
    raw["providers"]["openai"]["input_price_per_million"] = 1.0
    raw["providers"]["openai"]["output_price_per_million"] = 2.0
    config.write_text(yaml.safe_dump(raw), encoding="utf-8")

    run = prepare_run(config, "openai")
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["cost_estimate"]["input_price_per_million"] == 1.0
    assert manifest["cost_estimate"]["pricing_as_of"] == "project.yaml override"


class RecordingCancelAdapter(FakeAdapter):
    def __init__(self):
        super().__init__()
        self.cancelled_batch_ids: list[str] = []

    def cancel(self, batch_id):
        self.cancelled_batch_ids.append(batch_id)
        return super().cancel(batch_id)


class FailSecondCancelAdapter(RecordingCancelAdapter):
    def cancel(self, batch_id):
        if len(self.cancelled_batch_ids) == 1:
            raise RuntimeError("cancel unavailable")
        return super().cancel(batch_id)


def test_selective_cancel_preserves_prepared_segments_for_later_submission(
    example_config, tmp_path, monkeypatch
):
    adapter = RecordingCancelAdapter()
    run = _segmented_run(example_config, tmp_path, monkeypatch, adapter)
    submit_run(run, adapter, segment_range=(0, 2))
    submitted_ids = {
        segment["index"]: segment["remote_batch_id"]
        for segment in load_state(run)["segments"]
        if segment["remote_batch_id"]
    }

    state = cancel_run(run, adapter, segment_range=(0, 1))
    assert adapter.cancelled_batch_ids == [submitted_ids[0]]
    assert [segment["status"] for segment in state["segments"]] == [
        "cancelled",
        "submitted",
        "prepared",
        "prepared",
    ]
    assert state["status"] == "submitted"

    state = cancel_run(run, adapter, segment_range=(1, 2))
    assert adapter.cancelled_batch_ids == [submitted_ids[0], submitted_ids[1]]
    assert state["status"] == "prepared"

    state = submit_run(run, adapter)
    assert adapter.submissions == 4
    assert [segment["status"] for segment in state["segments"]] == [
        "cancelled",
        "cancelled",
        "submitted",
        "submitted",
    ]

    state = cancel_run(run, adapter)
    assert state["status"] == "cancelled"
    assert all(segment["status"] == "cancelled" for segment in state["segments"])


def test_explicit_cancel_rejects_ineligible_segments_before_provider_calls(
    example_config, tmp_path, monkeypatch
):
    adapter = RecordingCancelAdapter()
    run = _segmented_run(example_config, tmp_path, monkeypatch, adapter)
    submit_run(run, adapter, segment_range=(0, 1))

    with pytest.raises(ValueError, match="not eligible for cancellation"):
        cancel_run(run, adapter, segment_range=(0, 2))

    assert adapter.cancelled_batch_ids == []
    assert load_state(run)["segments"][0]["status"] == "submitted"


def test_explicit_cancel_rejects_already_cancelled_segment(
    example_config, tmp_path, monkeypatch
):
    adapter = RecordingCancelAdapter()
    run = _segmented_run(example_config, tmp_path, monkeypatch, adapter)
    submit_run(run, adapter, segment_range=(0, 1))
    cancel_run(run, adapter, segment_range=(0, 1))

    with pytest.raises(ValueError, match=r"0 \(cancelled\)"):
        cancel_run(run, adapter, segment_range=(0, 1))

    assert adapter.cancelled_batch_ids == ["fake-1"]


def test_cancel_failure_preserves_earlier_segment_state(
    example_config, tmp_path, monkeypatch
):
    adapter = FailSecondCancelAdapter()
    run = _segmented_run(example_config, tmp_path, monkeypatch, adapter)
    submit_run(run, adapter, segment_range=(0, 2))

    with pytest.raises(RuntimeError, match="cancel unavailable"):
        cancel_run(run, adapter, segment_range=(0, 2))

    state = load_state(run)
    assert state["segments"][0]["status"] == "cancelled"
    assert state["segments"][1]["status"] == "submitted"
    assert state["segments"][1]["error"] == "cancel unavailable"
    assert state["status"] == "submitted"


def test_cancel_run_cancels_submitted_segments_and_marks_run_cancelled(example_config, tmp_path, monkeypatch):
    adapter = RecordingCancelAdapter()
    monkeypatch.setattr(core, "get_provider", lambda name: adapter)
    run = prepare_run(_temporary_config(example_config, tmp_path), "openai", execution="batch")
    submit_run(run, adapter)
    submitted_batch_ids = [segment["remote_batch_id"] for segment in load_state(run)["segments"]]
    assert submitted_batch_ids

    state = cancel_run(run, adapter)

    assert state["status"] == "cancelled"
    assert sorted(adapter.cancelled_batch_ids) == sorted(submitted_batch_ids)
    assert all(segment["status"] == "cancelled" for segment in state["segments"])
    assert load_state(run)["status"] == "cancelled"


def test_cancel_run_on_unsubmitted_run_calls_provider_for_nothing(example_config, tmp_path, monkeypatch):
    adapter = RecordingCancelAdapter()
    monkeypatch.setattr(core, "get_provider", lambda name: adapter)
    run = prepare_run(_temporary_config(example_config, tmp_path), "openai", execution="batch")

    state = cancel_run(run, adapter)

    assert adapter.cancelled_batch_ids == []
    assert all(segment["status"] == "prepared" for segment in state["segments"])
