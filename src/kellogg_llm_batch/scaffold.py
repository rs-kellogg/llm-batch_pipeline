from __future__ import annotations

from pathlib import Path


PROJECT_YAML = """version: 1
project:
  name: my-coding-project
  description: Describe the research task.
input:
  path: data/input.csv
  format: auto
  id_column: record_id
  csv_encoding: utf-8
  fields_sent:
    text: text
  columns_preserved: []
  required_fields: [text]
  missing_required: error
task:
  rows_per_request: 10
  output_schema: schema.json
  max_input_tokens: 50000
  max_output_tokens: 2000
prompt:
  version: "1.0"
  system_file: prompts/system.txt
  user_file: prompts/user.txt
  context: {}
providers:
  openai:
    model: gpt-5-mini
  anthropic:
    model: claude-haiku-4-5
budget:
  max_estimated_usd: 10.0
output:
  write_parquet: true
  write_csv: true
  runs_directory: runs
"""

SCHEMA = """{
  "type": "object",
  "properties": {
    "label": {"type": "string"},
    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    "justification": {"type": "string"}
  },
  "required": ["label", "confidence", "justification"],
  "additionalProperties": false
}
"""


def scaffold_project(directory: str | Path) -> Path:
    root = Path(directory).expanduser().resolve()
    if root.exists() and any(root.iterdir()):
        raise FileExistsError(f"Refusing to overwrite non-empty directory: {root}")
    for name in ("data", "context", "prompts", "runs"):
        (root / name).mkdir(parents=True, exist_ok=True)
    (root / "project.yaml").write_text(PROJECT_YAML, encoding="utf-8")
    (root / "schema.json").write_text(SCHEMA, encoding="utf-8")
    (root / "prompts" / "system.txt").write_text("Code each record consistently using only the supplied evidence.\n", encoding="utf-8")
    (root / "prompts" / "user.txt").write_text("Records:\n\n${records_json}\n", encoding="utf-8")
    (root / "data" / "input.csv").write_text("record_id,text\nexample-001,Replace this row with research data.\n", encoding="utf-8")
    (root / ".gitignore").write_text("runs/\npilots/\n.env\n", encoding="utf-8")
    return root
