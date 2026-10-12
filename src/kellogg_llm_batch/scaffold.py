from __future__ import annotations

from pathlib import Path


# Keep these self-contained starter files in step with examples/minimal_task.
# tests/test_scaffold.py checks their content against that worked example.
PROJECT_YAML = """version: 1

project:
  name: my-task
  description: TODO describe what this task classifies or extracts.

input:
  path: data/input-data.csv
  format: auto
  id_column: id
  csv_encoding: utf-8
  fields_sent:
    text: text
  required_fields: []
  missing_required: error

task:
  rows_per_request: 1
  output_schema: schema.json
  max_output_tokens: 500

prompt:
  version: "1.0"
  system_file: prompts/system.txt
  user_file: prompts/user.txt

providers:
  openai:
    model: gpt-5-mini

budget:
  max_estimated_usd: 5.0

"""

SCHEMA = """{
  "type": "object",
  "properties": {
    "label": {
      "type": "string"
    },
    "rationale": {
      "type": "string"
    }
  },
  "required": [
    "label",
    "rationale"
  ],
  "additionalProperties": false
}
"""

INPUT_CSV = """id,text
EX-001,Replace this with one example record for your task.
EX-002,Add a second example record here.
"""

SYSTEM_PROMPT = """You are a careful classifier. Replace this prompt with instructions for your
own task: define the label values, the decision rules for choosing between
them, and what evidence in the record to use. Return only the JSON object
requested below. Return one result for every record_id.

"""

USER_PROMPT = """Classify every record below:

${records_json}

"""


def scaffold_project(directory: str | Path) -> Path:
    root = Path(directory).expanduser().resolve()
    if root.exists() and any(root.iterdir()):
        raise FileExistsError(f"Refusing to overwrite non-empty directory: {root}")
    for name in ("data", "context", "prompts", "runs"):
        (root / name).mkdir(parents=True, exist_ok=True)
    (root / "project.yaml").write_text(PROJECT_YAML, encoding="utf-8")
    (root / "schema.json").write_text(SCHEMA, encoding="utf-8")
    (root / "prompts" / "system.txt").write_text(SYSTEM_PROMPT, encoding="utf-8")
    (root / "prompts" / "user.txt").write_text(USER_PROMPT, encoding="utf-8")
    (root / "data" / "input-data.csv").write_text(INPUT_CSV, encoding="utf-8")
    (root / ".gitignore").write_text("runs/\n.env\n", encoding="utf-8")
    return root
