from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .base import ProviderAdapter
from ..models import BatchHandle, NormalizedResult


class AnthropicAdapter(ProviderAdapter):
    name = "anthropic"
    default_max_requests = 100_000
    default_max_bytes = 256_000_000

    def __init__(self, client=None):
        self._client = client

    @property
    def client(self):
        if self._client is None:
            from anthropic import Anthropic
            self._client = Anthropic()
        return self._client

    def build_payload(self, custom_id: str, model: str, system_prompt: str, user_prompt: str, schema: dict, max_output_tokens: int, options: dict[str, Any]) -> dict:
        params = {
            **options,
            "model": model,
            "max_tokens": max_output_tokens,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
            "output_config": {"format": {"type": "json_schema", "schema": schema}},
        }
        return {"custom_id": custom_id, "params": params}

    def submit(self, request_file: Path) -> BatchHandle:
        requests = self.read_jsonl(request_file)
        batch = self.client.messages.batches.create(requests=requests)
        return BatchHandle(provider=self.name, batch_id=batch.id, status=batch.processing_status)

    def status(self, batch_id: str) -> dict[str, Any]:
        batch = self.client.messages.batches.retrieve(batch_id)
        raw = batch.model_dump() if hasattr(batch, "model_dump") else dict(batch)
        status = raw.get("processing_status", "unknown")
        if status == "ended":
            # An ended batch can contain any mixture of succeeded, errored,
            # expired, and canceled rows. Always retrieve it so the common
            # processor can retain partial successes and classify each failure.
            canonical = "completed"
        else:
            canonical = "running"
        return {"provider_status": status, "state": canonical, "raw": raw}

    def cancel(self, batch_id: str) -> dict[str, Any]:
        batch = self.client.messages.batches.cancel(batch_id)
        return batch.model_dump() if hasattr(batch, "model_dump") else dict(batch)

    def download(self, batch_id: str, output_path: Path, error_path: Path) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as handle:
            for result in self.client.messages.batches.results(batch_id):
                obj = result.model_dump() if hasattr(result, "model_dump") else dict(result)
                handle.write(json.dumps(obj, ensure_ascii=False) + "\n")

    def normalize_line(self, obj: dict[str, Any]) -> NormalizedResult:
        custom_id = obj.get("custom_id", "")
        result = obj.get("result") or {}
        result_type = result.get("type", "unknown")
        if result_type != "succeeded":
            error = result.get("error") or {}
            status = "cancelled" if result_type == "canceled" else result_type if result_type in {"errored", "expired"} else "unknown"
            return NormalizedResult(custom_id=custom_id, status=status, error_type=error.get("type") or result_type, error_message=error.get("message"))
        message = result.get("message") or {}
        text = "\n".join(block.get("text", "") for block in message.get("content", []) if block.get("type") == "text")
        usage = message.get("usage") or {}
        return NormalizedResult(custom_id=custom_id, status="succeeded", response_text=text, model=message.get("model"), input_tokens=int(usage.get("input_tokens") or 0), output_tokens=int(usage.get("output_tokens") or 0))

    def run_sync(self, payload: dict[str, Any]) -> NormalizedResult:
        response = self.client.messages.create(**payload["params"])
        message = response.model_dump() if hasattr(response, "model_dump") else dict(response)
        return self.normalize_line({"custom_id": payload["custom_id"], "result": {"type": "succeeded", "message": message}})
