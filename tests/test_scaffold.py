from kellogg_llm_batch.scaffold import scaffold_project
from kellogg_llm_batch.validation import validate_project


def test_scaffold_is_immediately_valid(tmp_path):
    root = scaffold_project(tmp_path / "project")
    report = validate_project(root / "project.yaml")
    assert report["valid"] is True

