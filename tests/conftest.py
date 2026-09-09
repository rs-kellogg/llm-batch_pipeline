from __future__ import annotations

import json
from pathlib import Path

import pytest

from kellogg_llm_batch.models import BatchHandle, NormalizedResult
from kellogg_llm_batch.providers.base import ProviderAdapter


@pytest.fixture
def example_config() -> Path:
    return Path(__file__).parents[1] / "examples" / "grant_coding" / "project.yaml"


class FakeAdapter(ProviderAdapter):
    name = "openai"
    default_max_requests = 50_000
    default_max_bytes = 200_000_000

    def __init__(self):
        self.batches = {}
        self.submissions = 0

    def build_payload(self, custom_id, model, system_prompt, user_prompt, schema, max_output_tokens, options):
        return {"custom_id": custom_id, "body": {"model": model, "instructions": system_prompt, "input": user_prompt, "schema": schema}}

    def submit(self, request_file):
        self.submissions += 1
        batch_id = f"fake-{self.submissions}"
        self.batches[batch_id] = self.read_jsonl(request_file)
        return BatchHandle(provider="openai", batch_id=batch_id, input_file_id=f"file-{self.submissions}", status="in_progress")

    def status(self, batch_id):
        return {"provider_status": "completed", "state": "completed", "raw": {}}

    def cancel(self, batch_id):
        return {"id": batch_id, "status": "cancelled"}

    def download(self, batch_id, output_path, error_path):
        with output_path.open("w", encoding="utf-8") as handle:
            for payload in self.batches[batch_id]:
                records = json.loads(payload["body"]["input"].split("Classify every record below:", 1)[1].strip())
                results = [{"record_id": record["record_id"], "primary_label": "other", "secondary_label": None, "confidence": 0.8, "justification": "Synthetic test result."} for record in records]
                handle.write(json.dumps({"custom_id": payload["custom_id"], "status": "succeeded", "response_text": json.dumps({"results": results})}) + "\n")

    def normalize_line(self, obj):
        return NormalizedResult(custom_id=obj["custom_id"], status=obj["status"], response_text=obj.get("response_text"), model="fake-model", input_tokens=100, output_tokens=20)

    def run_sync(self, payload):
        records = json.loads(payload["body"]["input"].split("Classify every record below:", 1)[1].strip())
        results = [{"record_id": record["record_id"], "primary_label": "other", "secondary_label": None, "confidence": 0.8, "justification": "Synthetic test result."} for record in records]
        return NormalizedResult(custom_id=payload["custom_id"], status="succeeded", response_text=json.dumps({"results": results}), model="fake-model", input_tokens=100, output_tokens=20)
