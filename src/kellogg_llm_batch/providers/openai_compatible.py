from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .base import ProviderAdapter
from ..models import BatchHandle, NormalizedResult

DEFAULT_PLACEHOLDER_API_KEY = "sk-kllm-openai-compatible-placeholder"

_SYNC_ONLY_MESSAGE = (
    "The openai_compatible provider only supports execution='sync'; it has no batch "
    "submit/status/cancel/download implementation against a self-hosted Ollama/vLLM "
    "server. Prepare this provider with --execution sync (or a --sample-size/--ids-file "
    "pilot, which defaults to sync)."
)


class OpenAICompatibleAdapter(ProviderAdapter):
    name = "openai_compatible"
    default_max_requests = 100_000
    default_max_bytes = 512_000_000

    def __init__(self, client=None, *, base_url: str | None = None, api_key: str | None = None):
        self._client = client
        self._base_url = base_url
        self._api_key = api_key

    @property
    def client(self):
        if self._client is None:
            if not self._base_url:
                raise ValueError(
                    "providers.openai_compatible.base_url is required to use this provider; "
                    "point it at your Ollama or vLLM OpenAI-compatible endpoint (e.g. "
                    "http://localhost:11434/v1 for Ollama or http://localhost:8000/v1 for vLLM)."
                )
            from openai import OpenAI
            self._client = OpenAI(base_url=self._base_url, api_key=self._api_key or DEFAULT_PLACEHOLDER_API_KEY)
        return self._client

    def build_payload(self, custom_id: str, model: str, system_prompt: str, user_prompt: str, schema: dict, max_output_tokens: int, options: dict[str, Any]) -> dict:
        body = {
            **options,
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": max_output_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "kllm_results", "schema": schema, "strict": True},
            },
        }
        return {"custom_id": custom_id, "method": "POST", "url": "/v1/chat/completions", "body": body}

    def submit(self, request_file: Path) -> BatchHandle:
        raise NotImplementedError(_SYNC_ONLY_MESSAGE)

    def status(self, batch_id: str) -> dict[str, Any]:
        raise NotImplementedError(_SYNC_ONLY_MESSAGE)

    def cancel(self, batch_id: str) -> dict[str, Any]:
        raise NotImplementedError(_SYNC_ONLY_MESSAGE)

    def download(self, batch_id: str, output_path: Path, error_path: Path) -> None:
        raise NotImplementedError(_SYNC_ONLY_MESSAGE)

    def normalize_line(self, obj: dict[str, Any]) -> NormalizedResult:
        custom_id = obj.get("custom_id", "")
        if obj.get("error"):
            error = obj["error"]
            return NormalizedResult(custom_id=custom_id, status="errored", error_type=error.get("code") or error.get("type"), error_message=error.get("message"))
        response = obj.get("response") or {}
        if int(response.get("status_code", 0)) >= 400:
            return NormalizedResult(custom_id=custom_id, status="errored", error_type="http_error", error_message=json.dumps(response.get("body")))
        body = response.get("body") or {}
        choices = body.get("choices") or []
        message = (choices[0].get("message") if choices else None) or {}
        text = message.get("content")
        if not isinstance(text, str):
            return NormalizedResult(custom_id=custom_id, status="errored", error_type="missing_output_text", error_message="Chat Completions response had no message content", model=body.get("model"))
        usage = body.get("usage") or {}
        return NormalizedResult(custom_id=custom_id, status="succeeded", response_text=text, model=body.get("model"), input_tokens=int(usage.get("prompt_tokens") or 0), output_tokens=int(usage.get("completion_tokens") or 0))

    def run_sync(self, payload: dict[str, Any]) -> NormalizedResult:
        response = self.client.chat.completions.create(**payload["body"])
        body = response.model_dump() if hasattr(response, "model_dump") else dict(response)
        line = {"custom_id": payload["custom_id"], "response": {"status_code": 200, "body": body}}
        return self.normalize_line(line)
