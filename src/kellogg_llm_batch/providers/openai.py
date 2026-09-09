from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .base import ProviderAdapter
from ..models import BatchHandle, NormalizedResult


def _extract_text(body: dict[str, Any]) -> str:
    if isinstance(body.get("output_text"), str):
        return body["output_text"]
    texts: list[str] = []
    for item in body.get("output", []):
        for content in item.get("content", []):
            if content.get("type") in {"output_text", "text"} and isinstance(content.get("text"), str):
                texts.append(content["text"])
    if not texts:
        raise ValueError("OpenAI response contains no output text")
    return "\n".join(texts)


class OpenAIAdapter(ProviderAdapter):
    name = "openai"
    default_max_requests = 50_000
    default_max_bytes = 200_000_000

    def __init__(self, client=None):
        self._client = client

    @property
    def client(self):
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI()
        return self._client

    def build_payload(self, custom_id: str, model: str, system_prompt: str, user_prompt: str, schema: dict, max_output_tokens: int, options: dict[str, Any]) -> dict:
        body = {
            **options,
            "model": model,
            "instructions": system_prompt,
            "input": user_prompt,
            "max_output_tokens": max_output_tokens,
            "text": {"format": {"type": "json_schema", "name": "kllm_results", "schema": schema, "strict": True}},
        }
        return {"custom_id": custom_id, "method": "POST", "url": "/v1/responses", "body": body}

    def submit(self, request_file: Path) -> BatchHandle:
        with request_file.open("rb") as handle:
            uploaded = self.client.files.create(file=handle, purpose="batch")
        batch = self.client.batches.create(input_file_id=uploaded.id, endpoint="/v1/responses", completion_window="24h")
        return BatchHandle(provider=self.name, batch_id=batch.id, input_file_id=uploaded.id, status=batch.status)

    def status(self, batch_id: str) -> dict[str, Any]:
        batch = self.client.batches.retrieve(batch_id)
        raw = batch.model_dump() if hasattr(batch, "model_dump") else dict(batch)
        status = raw.get("status", "unknown")
        # Terminal batches can contain partial output and/or an error file, so
        # route all of them through download and row-level auditing.
        canonical = "completed" if status in {"completed", "failed", "expired", "cancelled"} else "running"
        return {"provider_status": status, "state": canonical, "raw": raw}

    def cancel(self, batch_id: str) -> dict[str, Any]:
        batch = self.client.batches.cancel(batch_id)
        return batch.model_dump() if hasattr(batch, "model_dump") else dict(batch)

    def download(self, batch_id: str, output_path: Path, error_path: Path) -> None:
        batch = self.client.batches.retrieve(batch_id)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if getattr(batch, "output_file_id", None):
            output_path.write_bytes(self.client.files.content(batch.output_file_id).content)
        if getattr(batch, "error_file_id", None):
            error_path.write_bytes(self.client.files.content(batch.error_file_id).content)

    def normalize_line(self, obj: dict[str, Any]) -> NormalizedResult:
        custom_id = obj.get("custom_id", "")
        if obj.get("error"):
            error = obj["error"]
            return NormalizedResult(custom_id=custom_id, status="errored", error_type=error.get("code") or error.get("type"), error_message=error.get("message"))
        response = obj.get("response") or {}
        if int(response.get("status_code", 0)) >= 400:
            return NormalizedResult(custom_id=custom_id, status="errored", error_type="http_error", error_message=json.dumps(response.get("body")))
        body = response.get("body") or {}
        usage = body.get("usage") or {}
        try:
            text = _extract_text(body)
        except ValueError as exc:
            return NormalizedResult(custom_id=custom_id, status="errored", error_type="missing_output_text", error_message=str(exc), model=body.get("model"))
        return NormalizedResult(custom_id=custom_id, status="succeeded", response_text=text, model=body.get("model"), input_tokens=int(usage.get("input_tokens") or 0), output_tokens=int(usage.get("output_tokens") or 0))

    def run_sync(self, payload: dict[str, Any]) -> NormalizedResult:
        response = self.client.responses.create(**payload["body"])
        body = response.model_dump() if hasattr(response, "model_dump") else dict(response)
        line = {"custom_id": payload["custom_id"], "response": {"status_code": 200, "body": body}}
        return self.normalize_line(line)
