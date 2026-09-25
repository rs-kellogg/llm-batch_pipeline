from __future__ import annotations

from pathlib import Path


# Keep these self-contained starter files in step with examples/grant_coding.
# tests/test_scaffold.py checks their content against that worked example.
PROJECT_YAML = """version: 1

project:
  name: grant-topic-coding
  description: Classify synthetic grant abstracts using a small codebook.

input:
  path: data/grants.csv
  format: auto
  id_column: grant_id
  csv_encoding: utf-8
  fields_sent:
    project_title: project_title
    abstract: abstract
  columns_preserved:
    - year
    - investigator
    - source_file
  required_fields:
    - abstract
  missing_required: error
  field_limits:
    abstract:
      overflow: error

task:
  rows_per_request: 3
  output_schema: schema.json
  max_input_tokens: 50000
  max_output_tokens: 1200

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
  max_estimated_usd: 5.0

evaluation:
  random_seed: 42

output:
  write_parquet: true
  write_csv: true
  runs_directory: runs

"""

SCHEMA = """{
  "type": "object",
  "properties": {
    "primary_label": {
      "type": "string",
      "enum": ["financial", "organizational", "technical", "other"]
    },
    "secondary_label": {
      "type": ["string", "null"],
      "enum": ["financial", "organizational", "technical", "other", null]
    },
    "confidence": {
      "type": "number",
      "minimum": 0,
      "maximum": 1
    },
    "justification": {
      "type": "string"
    }
  },
  "required": ["primary_label", "secondary_label", "confidence", "justification"],
  "additionalProperties": false
}

"""

CODEBOOK_CSV = """label,definition
financial,Research primarily about markets pricing investment accounting or financial decisions
organizational,Research primarily about organizations teams leadership management or workplace behavior
technical,Research primarily about computing engineering data systems or technical methods
other,Research whose primary topic does not fit the other categories

"""

INPUT_CSV = """grant_id,project_title,abstract,year,investigator,source_file
GRANT-001,Team learning after product failures,Studies how teams update routines and coordinate work after unsuccessful product launches.,2025,Alex Rivera,synthetic_2025.csv
GRANT-002,Transparent pricing in digital markets,Tests how disclosure of seller fees changes consumer search and market prices.,2025,Morgan Lee,synthetic_2025.csv
GRANT-003,Efficient models for document classification,Develops compact machine-learning methods for classifying large collections of research documents.,2026,Sam Patel,synthetic_2026.csv
GRANT-004,Leadership transitions and employee voice,Examines whether leadership succession changes employees' willingness to raise operational concerns.,2026,Jordan Kim,synthetic_2026.csv
GRANT-005,Investor attention during earnings calls,Measures how shifts in investor attention affect reactions to corporate earnings disclosures.,2025,Taylor Brooks,synthetic_2025.csv
GRANT-006,Auditing data pipelines at scale,Creates technical methods to detect schema drift and missing records in large data pipelines.,2026,Casey Nguyen,synthetic_2026.csv
GRANT-007,Community gardens and neighborhood trust,Evaluates whether shared garden programs strengthen trust among urban neighbors.,2025,Riley Chen,synthetic_2025.csv
GRANT-008,Coordination in hybrid work groups,Studies communication routines and coordination quality in teams working across office and remote settings.,2026,Drew Williams,synthetic_2026.csv
GRANT-009,Forecasting liquidity with alternative data,Builds forecasting models that use transaction signals to estimate market liquidity.,2026,Jamie Ortiz,synthetic_2026.csv
GRANT-010,Secure storage for collaborative analytics,Designs privacy-preserving storage systems for researchers sharing large analytical datasets.,2025,Avery Johnson,synthetic_2025.csv

"""

SYSTEM_PROMPT = """You are a rigorous research-coding assistant. Classify every supplied grant using the codebook definitions consistently. Base each decision only on the title and abstract. Do not infer unsupported facts. Choose exactly one primary label, use a secondary label only when a second theme is substantively present, and provide one concise evidence-grounded sentence. Return one result for every record_id.

"""

USER_PROMPT = """Apply this codebook:

${codebook_json}

Classify every record below:

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
    (root / "data" / "grants.csv").write_text(INPUT_CSV, encoding="utf-8")
    (root / "context" / "codebook.csv").write_text(CODEBOOK_CSV, encoding="utf-8")
    (root / ".gitignore").write_text("runs/\n.env\n", encoding="utf-8")
    return root
