from typer.testing import CliRunner
import json
from pathlib import Path
import yaml

import kellogg_llm_batch.core as core
from kellogg_llm_batch.cli import app
from conftest import FakeAdapter


runner = CliRunner()


def test_help_lists_workflow_commands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("validate", "prepare", "submit", "status", "sync", "audit", "retry", "merge", "compare"):
        assert command in result.stdout
    assert "pilot" not in result.stdout


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
    raw["input"]["path"] = str(example_config.parent / "data" / "grants.csv")
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
    raw["input"]["path"] = str(example_config.parent / "data" / "grants.csv")
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
