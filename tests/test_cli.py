from typer.testing import CliRunner
import json
from pathlib import Path
from types import SimpleNamespace
import yaml

import kellogg_llm_batch.core as core
from kellogg_llm_batch.cli import app
from kellogg_llm_batch.core import prepare_run, submit_run
from kellogg_llm_batch.state import load_state
from conftest import FakeAdapter


runner = CliRunner()


def test_help_lists_workflow_commands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("gui", "validate", "prepare", "submit", "status", "sync", "audit", "retry", "merge", "compare"):
        assert command in result.stdout
    assert "pilot" not in result.stdout


def test_init_command_uses_input_data_filename(tmp_path):
    project = tmp_path / "project"
    result = runner.invoke(app, ["init", str(project)])

    assert result.exit_code == 0
    assert (project / "data" / "input-data.csv").is_file()
    assert not (project / "data" / "grants.csv").exists()
    config = yaml.safe_load((project / "project.yaml").read_text(encoding="utf-8"))
    assert config["input"]["path"] == "data/input-data.csv"


def test_gui_command_launches_local_streamlit(tmp_path, monkeypatch):
    captured = {}
    monkeypatch.setattr("kellogg_llm_batch.cli.importlib.util.find_spec", lambda name: object())
    project = tmp_path / "project"
    project.mkdir()
    (project / "project.yaml").write_text("version: 1\n", encoding="utf-8")

    def fake_run(command, env, check):
        captured.update(command=command, env=env, check=check)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("kellogg_llm_batch.cli.subprocess.run", fake_run)
    result = runner.invoke(app, ["gui", str(project), "--port", "8765", "--no-browser"])

    assert result.exit_code == 0
    assert captured["env"]["KLLM_GUI_PROJECT_DIR"] == str(project.resolve())
    assert "127.0.0.1" in captured["command"]
    assert "8765" in captured["command"]
    assert "--server.headless" in captured["command"]
    assert captured["command"][captured["command"].index("--server.headless") + 1] == "true"
    assert "--theme.base" in captured["command"]
    assert captured["command"][captured["command"].index("--theme.base") + 1] == "light"
    assert captured["command"][captured["command"].index("--theme.baseFontSize") + 1] == "18"
    assert captured["command"][captured["command"].index("--theme.primaryColor") + 1] == "#4E2A84"
    assert captured["command"][captured["command"].index("--theme.baseRadius") + 1] == "medium"


def test_gui_command_explains_missing_dependency(tmp_path, monkeypatch):
    monkeypatch.setattr("kellogg_llm_batch.cli.importlib.util.find_spec", lambda name: None)
    result = runner.invoke(app, ["gui", str(tmp_path / "project")])
    assert result.exit_code == 1
    assert "Streamlit is missing from this environment" in result.stdout
    assert "python -m pip install" in result.stdout
    assert (
        "kellogg-llm-batch @ git+https://github.com/rs-kellogg/llm-batch_pipeline.git"
        in " ".join(result.stdout.split())
    )


def test_gui_command_requires_initialized_project(tmp_path, monkeypatch):
    monkeypatch.setattr("kellogg_llm_batch.cli.importlib.util.find_spec", lambda name: object())
    project = tmp_path / "not-initialized"
    result = runner.invoke(app, ["gui", str(project)])

    assert result.exit_code == 1
    assert "Initialized project not found" in result.stdout
    assert "Create it first with: kllm-batch init" in " ".join(result.stdout.split())


def test_example_validate_command(example_config):
    result = runner.invoke(app, ["validate", "-c", str(example_config)])
    assert result.exit_code == 0
    assert "Validation passed" in result.stdout


def test_documentation_does_not_expose_personal_home_paths():
    root = Path(__file__).parents[1]
    documentation = [root / "README.md", *root.glob("plan*.md"), *root.glob("docs/*.md"), *root.glob("examples/*/README.md")]
    for path in documentation:
        assert "/Users/" not in path.read_text(encoding="utf-8"), path


def test_documented_cli_workflow_with_mock_provider(example_config, tmp_path, monkeypatch):
    raw = yaml.safe_load(example_config.read_text())
    raw["input"]["path"] = str(example_config.parent / "data" / "input-data.csv")
    raw["task"]["output_schema"] = str(example_config.parent / "schema.json")
    raw["prompt"]["system_file"] = str(example_config.parent / "prompts" / "system.txt")
    raw["prompt"]["user_file"] = str(example_config.parent / "prompts" / "user.txt")
    raw["prompt"]["context"]["codebook"]["path"] = str(example_config.parent / "context" / "codebook.csv")
    raw["output"]["runs_directory"] = str(tmp_path / "runs")
    config = tmp_path / "project.yaml"
    config.write_text(yaml.safe_dump(raw), encoding="utf-8")
    fake = FakeAdapter()
    monkeypatch.setattr(core, "get_provider", lambda name: fake)

    assert runner.invoke(app, ["validate", "-c", str(config)]).exit_code == 0
    assert runner.invoke(app, ["prepare", "-c", str(config), "--provider", "openai", "--sample-size", "4", "--seed", "42"]).exit_code == 0
    sample_run = next((tmp_path / "runs").iterdir())
    assert json.loads((sample_run / "manifest.json").read_text())["execution"] == "sync"
    sample_submit = runner.invoke(app, ["submit", str(sample_run), "--yes"])
    assert sample_submit.exit_code == 0
    assert "Resuming partial" not in sample_submit.stdout
    assert "Completed 1 out of 2 synchronous requests" in sample_submit.stdout
    assert "Completed 2 out of 2 synchronous requests" in sample_submit.stdout
    sync_calls = fake.sync_calls
    repeated_submit = runner.invoke(app, ["submit", str(sample_run)])
    assert repeated_submit.exit_code == 0
    assert "2 of 2 synchronous requests already finished" in repeated_submit.stdout
    assert "No API requests were run and no new run was created" in " ".join(repeated_submit.stdout.split())
    assert fake.sync_calls == sync_calls
    raw_output = sample_run / "raw_responses" / "segment_0000_output.jsonl"
    saved_lines = raw_output.read_text(encoding="utf-8").splitlines()
    raw_output.write_text(saved_lines[0] + "\n", encoding="utf-8")
    tampered_submit = runner.invoke(app, ["submit", str(sample_run)])
    tampered_output = " ".join(tampered_submit.stdout.split())
    assert tampered_submit.exit_code == 1
    assert "Integrity warning" in tampered_output
    assert "only 1 of 2 expected responses exist in raw_responses" in tampered_output
    assert "Missing request IDs: request_00000001" in tampered_output
    assert "No API requests were run" in tampered_output
    assert "--ids-file rerun_ids.txt" in tampered_output
    assert fake.sync_calls == sync_calls
    assert runner.invoke(app, ["prepare", "-c", str(config), "--provider", "openai"]).exit_code == 0
    run = next(path for path in (tmp_path / "runs").iterdir() if json.loads((path / "manifest.json").read_text())["purpose"] == "production")
    assert runner.invoke(app, ["submit", str(run), "--yes"]).exit_code == 0
    assert runner.invoke(app, ["status", str(run)]).exit_code == 0
    assert runner.invoke(app, ["sync", str(run)]).exit_code == 0
    assert runner.invoke(app, ["audit", str(run)]).exit_code == 0
    assert runner.invoke(app, ["merge", str(run)]).exit_code == 0
    assert runner.invoke(app, ["compare", str(run), str(run)]).exit_code == 0


def test_submit_warns_only_when_resuming_partial_sync_run(example_config, tmp_path, monkeypatch):
    raw = yaml.safe_load(example_config.read_text())
    raw["input"]["path"] = str(example_config.parent / "data" / "input-data.csv")
    raw["task"]["output_schema"] = str(example_config.parent / "schema.json")
    raw["prompt"]["system_file"] = str(example_config.parent / "prompts" / "system.txt")
    raw["prompt"]["user_file"] = str(example_config.parent / "prompts" / "user.txt")
    raw["prompt"]["context"]["codebook"]["path"] = str(example_config.parent / "context" / "codebook.csv")
    raw["output"]["runs_directory"] = str(tmp_path / "runs")
    config = tmp_path / "project.yaml"
    config.write_text(yaml.safe_dump(raw), encoding="utf-8")
    fake = FakeAdapter()
    monkeypatch.setattr(core, "get_provider", lambda name: fake)

    prepared = runner.invoke(
        app,
        ["prepare", "-c", str(config), "--provider", "openai", "--sample-size", "4", "--seed", "42"],
    )
    assert prepared.exit_code == 0
    run = next((tmp_path / "runs").iterdir())
    payload = fake.read_jsonl(run / "api_requests" / "segment_0000.jsonl")[0]
    outcome = fake.run_sync(payload)
    fake.sync_calls = 0
    raw_dir = run / "raw_responses"
    raw_dir.mkdir()
    (raw_dir / "segment_0000_output.jsonl").write_text(
        json.dumps({"_kllm_normalized": outcome.model_dump()}) + "\n",
        encoding="utf-8",
    )

    resumed = runner.invoke(app, ["submit", str(run)], input="y\n")
    assert resumed.exit_code == 0
    output = " ".join(resumed.stdout.split())
    assert "Resuming partial synchronous run: 1 of 2 requests" in output
    assert "1 remains" in output
    assert "one unrecorded request could run again" in output
    assert "Execute 1 remaining sync request with estimated maximum remaining cost $0.0025?" in output
    assert "Completed 2 out of 2 synchronous requests" in output
    assert fake.sync_calls == 1


def test_cancel_command_cancels_a_submitted_run(example_config, tmp_path, monkeypatch):
    raw = yaml.safe_load(example_config.read_text())
    raw["input"]["path"] = str(example_config.parent / "data" / "input-data.csv")
    raw["task"]["output_schema"] = str(example_config.parent / "schema.json")
    raw["prompt"]["system_file"] = str(example_config.parent / "prompts" / "system.txt")
    raw["prompt"]["user_file"] = str(example_config.parent / "prompts" / "user.txt")
    raw["prompt"]["context"]["codebook"]["path"] = str(example_config.parent / "context" / "codebook.csv")
    raw["output"]["runs_directory"] = str(tmp_path / "runs")
    config = tmp_path / "project.yaml"
    config.write_text(yaml.safe_dump(raw), encoding="utf-8")
    fake = FakeAdapter()
    monkeypatch.setattr(core, "get_provider", lambda name: fake)

    assert runner.invoke(app, ["prepare", "-c", str(config), "--provider", "openai", "--execution", "batch"]).exit_code == 0
    run = next((tmp_path / "runs").iterdir())
    assert runner.invoke(app, ["submit", str(run), "--yes"]).exit_code == 0

    result = runner.invoke(app, ["cancel", str(run)])

    assert result.exit_code == 0, result.stdout
    state = json.loads((run / "state.json").read_text(encoding="utf-8"))
    assert state["status"] == "cancelled"
    assert all(segment["status"] == "cancelled" for segment in state["segments"])


def test_cancel_command_reports_errors_for_unknown_run(tmp_path):
    result = runner.invoke(app, ["cancel", str(tmp_path / "missing-run")])
    assert result.exit_code == 1
    assert "Run not found" in result.stdout


def test_submit_command_targets_segment_range(
    example_config, tmp_path, monkeypatch
):
    raw = yaml.safe_load(example_config.read_text())
    raw["input"]["path"] = str(example_config.parent / "data" / "input-data.csv")
    raw["task"]["output_schema"] = str(example_config.parent / "schema.json")
    raw["prompt"]["system_file"] = str(example_config.parent / "prompts" / "system.txt")
    raw["prompt"]["user_file"] = str(example_config.parent / "prompts" / "user.txt")
    raw["prompt"]["context"]["codebook"]["path"] = str(example_config.parent / "context" / "codebook.csv")
    raw["providers"]["openai"]["max_requests_per_batch"] = 1
    raw["output"]["runs_directory"] = str(tmp_path / "runs")
    config = tmp_path / "project.yaml"
    config.write_text(yaml.safe_dump(raw), encoding="utf-8")
    fake = FakeAdapter()
    monkeypatch.setattr(core, "get_provider", lambda name: fake)
    run = prepare_run(config, "openai", execution="batch")

    submitted = runner.invoke(
        app,
        ["submit", str(run), "--seg_start", "0", "--seg_end", "2"],
        input="y\n",
    )

    assert submitted.exit_code == 0, submitted.stdout
    normalized = " ".join(submitted.stdout.split())
    assert "Requested range [0, 2) resolves to segments 0, 1" in normalized
    assert "Submit batch segments 0, 1 containing 2 requests?" in normalized
    assert "full prepared run's estimated maximum cost" in submitted.stdout
    assert fake.submissions == 2
    assert [segment["status"] for segment in load_state(run)["segments"]] == [
        "submitted",
        "submitted",
        "prepared",
        "prepared",
    ]

    repeated = runner.invoke(
        app,
        ["submit", str(run), "--seg_start", "0", "--seg_end", "2", "--yes"],
    )
    assert repeated.exit_code == 0
    assert "already submitted" in repeated.stdout
    assert "No API requests were run" in " ".join(repeated.stdout.split())
    assert fake.submissions == 2

    open_ended = runner.invoke(
        app,
        ["submit", str(run), "--seg_start", "2", "--seg_end", "-1"],
        input="y\n",
    )
    assert open_ended.exit_code == 0, open_ended.stdout
    assert "-1 means through end" in " ".join(open_ended.stdout.split())
    assert fake.submissions == 4
    assert all(
        segment["status"] == "submitted" for segment in load_state(run)["segments"]
    )


def test_segment_range_cli_validation_happens_before_provider_calls(
    example_config, tmp_path, monkeypatch
):
    raw = yaml.safe_load(example_config.read_text())
    raw["input"]["path"] = str(example_config.parent / "data" / "input-data.csv")
    raw["task"]["output_schema"] = str(example_config.parent / "schema.json")
    raw["prompt"]["system_file"] = str(example_config.parent / "prompts" / "system.txt")
    raw["prompt"]["user_file"] = str(example_config.parent / "prompts" / "user.txt")
    raw["prompt"]["context"]["codebook"]["path"] = str(example_config.parent / "context" / "codebook.csv")
    raw["output"]["runs_directory"] = str(tmp_path / "runs")
    config = tmp_path / "project.yaml"
    config.write_text(yaml.safe_dump(raw), encoding="utf-8")
    fake = FakeAdapter()
    monkeypatch.setattr(core, "get_provider", lambda name: fake)

    batch_run = prepare_run(config, "openai", execution="batch")
    invalid = runner.invoke(
        app,
        ["submit", str(batch_run), "--seg_start", "99", "--seg_end", "-1", "--yes"],
    )
    assert invalid.exit_code == 1
    assert "outside the available indexes" in invalid.stdout
    assert fake.submissions == 0

    missing_end = runner.invoke(
        app, ["submit", str(batch_run), "--seg_start", "0", "--yes"]
    )
    assert missing_end.exit_code == 1
    assert "must be provided together" in missing_end.stdout
    assert fake.submissions == 0

    missing_start = runner.invoke(
        app, ["submit", str(batch_run), "--seg_end", "-1", "--yes"]
    )
    assert missing_start.exit_code == 1
    assert "must be provided together" in missing_start.stdout
    assert fake.submissions == 0

    removed_option = runner.invoke(
        app, ["submit", str(batch_run), "--segment", "0", "--yes"]
    )
    assert removed_option.exit_code != 0
    assert fake.submissions == 0

    sync_run_dir = prepare_run(
        config, "openai", sample_size=4, seed=42, execution="sync"
    )
    synchronous = runner.invoke(
        app,
        ["submit", str(sync_run_dir), "--seg_start", "0", "--seg_end", "1", "--yes"],
    )
    assert synchronous.exit_code == 1
    assert "available only for batch runs" in synchronous.stdout
    assert fake.sync_calls == 0


def test_cancel_command_targets_one_segment(
    example_config, tmp_path, monkeypatch
):
    class RecordingAdapter(FakeAdapter):
        def __init__(self):
            super().__init__()
            self.cancelled = []

        def cancel(self, batch_id):
            self.cancelled.append(batch_id)
            return super().cancel(batch_id)

    raw = yaml.safe_load(example_config.read_text())
    raw["input"]["path"] = str(example_config.parent / "data" / "input-data.csv")
    raw["task"]["output_schema"] = str(example_config.parent / "schema.json")
    raw["prompt"]["system_file"] = str(example_config.parent / "prompts" / "system.txt")
    raw["prompt"]["user_file"] = str(example_config.parent / "prompts" / "user.txt")
    raw["prompt"]["context"]["codebook"]["path"] = str(example_config.parent / "context" / "codebook.csv")
    raw["providers"]["openai"]["max_requests_per_batch"] = 1
    raw["output"]["runs_directory"] = str(tmp_path / "runs")
    config = tmp_path / "project.yaml"
    config.write_text(yaml.safe_dump(raw), encoding="utf-8")
    adapter = RecordingAdapter()
    monkeypatch.setattr(core, "get_provider", lambda name: adapter)
    run = prepare_run(config, "openai", execution="batch")
    submit_run(run, adapter, segment_range=(0, 2))
    target_batch_id = load_state(run)["segments"][1]["remote_batch_id"]

    result = runner.invoke(
        app, ["cancel", str(run), "--seg_start", "1", "--seg_end", "2"]
    )

    assert result.exit_code == 0, result.stdout
    assert adapter.cancelled == [target_batch_id]
    assert [segment["status"] for segment in load_state(run)["segments"]] == [
        "submitted",
        "cancelled",
        "prepared",
        "prepared",
    ]


def test_sync_watch_prints_each_poll_status(example_config, tmp_path, monkeypatch):
    class DelayedAdapter(FakeAdapter):
        def __init__(self):
            super().__init__()
            self.status_checks = 0

        def status(self, batch_id):
            self.status_checks += 1
            if self.status_checks == 1:
                return {"provider_status": "in_progress", "state": "running", "raw": {}}
            return {"provider_status": "completed", "state": "completed", "raw": {}}

    raw = yaml.safe_load(example_config.read_text())
    raw["input"]["path"] = str(example_config.parent / "data" / "input-data.csv")
    raw["task"]["output_schema"] = str(example_config.parent / "schema.json")
    raw["prompt"]["system_file"] = str(example_config.parent / "prompts" / "system.txt")
    raw["prompt"]["user_file"] = str(example_config.parent / "prompts" / "user.txt")
    raw["prompt"]["context"]["codebook"]["path"] = str(example_config.parent / "context" / "codebook.csv")
    raw["output"]["runs_directory"] = str(tmp_path / "runs")
    config = tmp_path / "project.yaml"
    config.write_text(yaml.safe_dump(raw), encoding="utf-8")
    fake = DelayedAdapter()
    monkeypatch.setattr(core, "get_provider", lambda name: fake)
    monkeypatch.setattr(core.time, "sleep", lambda seconds: None)

    assert runner.invoke(app, ["prepare", "-c", str(config), "--provider", "openai"]).exit_code == 0
    run = next((tmp_path / "runs").iterdir())
    assert runner.invoke(app, ["submit", str(run), "--yes"]).exit_code == 0
    watched = runner.invoke(app, ["sync", str(run), "--watch", "--poll-seconds", "7"])
    output = " ".join(watched.stdout.split())

    assert watched.exit_code == 0
    assert "run running | segment 0: in_progress | next check in 7s" in output
    assert "run completed | segment 0: completed" in output
    assert fake.status_checks == 2
