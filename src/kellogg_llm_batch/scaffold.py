from __future__ import annotations

from pathlib import Path


PROJECT_YAML = """version: 1
project:
  name: grant-topic-coding
  description: Classify synthetic grant abstracts by research topic.
input:
  path: data/input.csv
  format: auto
  id_column: record_id
  csv_encoding: utf-8
  fields_sent:
    project_title: project_title
    abstract: abstract
  columns_preserved: [year]
  required_fields: [project_title, abstract]
  missing_required: error
task:
  rows_per_request: 2
  output_schema: schema.json
  max_input_tokens: 50000
  max_output_tokens: 2000
prompt:
  version: "1.0"
  system_file: prompts/system.txt
  user_file: prompts/user.txt
  context:
    codebook:
      path: context/codebook.csv
      format: csv
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
    "label": {"type": "string", "enum": ["financial", "organizational", "technical", "other"]},
    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    "justification": {"type": "string"}
  },
  "required": ["label", "confidence", "justification"],
  "additionalProperties": false
}
"""

CODEBOOK_CSV = """label,definition
financial,Research primarily about markets pricing investment accounting or financial decisions
organizational,Research primarily about organizations teams leadership management or workplace behavior
technical,Research primarily about computing engineering data systems or technical methods
other,Research whose primary topic does not fit the other categories
"""

INPUT_CSV = """record_id,project_title,abstract,year
GRANT-001,Team learning after product failures,Studies how teams update routines and coordinate work after unsuccessful product launches.,2025
GRANT-002,Transparent pricing in digital markets,Tests how disclosure of seller fees changes consumer search and market prices.,2025
GRANT-003,Efficient models for document classification,Develops compact machine-learning methods for classifying large collections of research documents.,2026
GRANT-004,Leadership transitions and employee voice,Examines whether leadership succession changes employees willingness to raise operational concerns.,2026
GRANT-005,Investor attention during earnings calls,Measures how shifts in investor attention affect reactions to corporate earnings disclosures.,2025
GRANT-006,Auditing data pipelines at scale,Creates technical methods to detect schema drift and missing records in large data pipelines.,2026
GRANT-007,Community gardens and neighborhood trust,Evaluates whether shared garden programs strengthen trust among urban neighbors.,2025
GRANT-008,Transit routes and clinic access,Studies whether new bus routes improve access to preventive health services in underserved neighborhoods.,2026
"""

SYSTEM_PROMPT = (
    "You are a rigorous research-coding assistant. Classify each grant using only its "
    "title and abstract and the supplied codebook. Choose exactly one primary topic "
    "from the codebook for each record_id. Give a confidence between 0 and 1 and "
    "one concise evidence-based justification. Return one result for every record_id.\n"
)

USER_PROMPT = "Apply this codebook:\n\n${codebook_json}\n\nClassify every grant below:\n\n${records_json}\n"


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
    (root / "data" / "input.csv").write_text(INPUT_CSV, encoding="utf-8")
    (root / "context" / "codebook.csv").write_text(CODEBOOK_CSV, encoding="utf-8")
    (root / ".gitignore").write_text("runs/\n.env\n", encoding="utf-8")
    return root
