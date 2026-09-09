from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from ..models import BatchHandle, NormalizedResult


class ProviderAdapter(ABC):
    name: str
    default_max_requests: int
    default_max_bytes: int

    @abstractmethod
    def build_payload(self, custom_id: str, model: str, system_prompt: str, user_prompt: str, schema: dict, max_output_tokens: int, options: dict[str, Any]) -> dict: ...

    @abstractmethod
    def submit(self, request_file: Path) -> BatchHandle: ...

    @abstractmethod
    def status(self, batch_id: str) -> dict[str, Any]: ...

    @abstractmethod
    def cancel(self, batch_id: str) -> dict[str, Any]: ...

    @abstractmethod
    def download(self, batch_id: str, output_path: Path, error_path: Path) -> None: ...

    @abstractmethod
    def normalize_line(self, obj: dict[str, Any]) -> NormalizedResult: ...

    @abstractmethod
    def run_sync(self, payload: dict[str, Any]) -> NormalizedResult: ...

    @staticmethod
    def read_jsonl(path: Path) -> list[dict[str, Any]]:
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def get_provider(name: str) -> ProviderAdapter:
    if name == "openai":
        from .openai import OpenAIAdapter
        return OpenAIAdapter()
    if name == "anthropic":
        from .anthropic import AnthropicAdapter
        return AnthropicAdapter()
    raise ValueError(f"Unsupported provider: {name}")

