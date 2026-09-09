import json
from types import SimpleNamespace

from kellogg_llm_batch.providers.anthropic import AnthropicAdapter
from kellogg_llm_batch.providers.openai import OpenAIAdapter


SCHEMA = {"type": "object", "properties": {}, "required": [], "additionalProperties": False}


def test_openai_payload_uses_responses_structured_output():
    payload = OpenAIAdapter(client=object()).build_payload("request_1", "gpt-5-mini", "system", "user", SCHEMA, 100, {})
    assert payload["url"] == "/v1/responses"
    assert payload["body"]["text"]["format"]["schema"] == SCHEMA


def test_anthropic_payload_uses_output_config():
    payload = AnthropicAdapter(client=object()).build_payload("request_1", "claude-haiku-4-5", "system", "user", SCHEMA, 100, {})
    assert payload["params"]["output_config"]["format"]["schema"] == SCHEMA
    assert payload["params"]["messages"][0]["content"] == "user"


def test_anthropic_ended_batch_is_downloadable_even_when_all_rows_failed():
    class EndedClient:
        class Messages:
            class Batches:
                @staticmethod
                def retrieve(batch_id):
                    return {"processing_status": "ended", "request_counts": {"succeeded": 0, "errored": 2}}

            batches = Batches()

        messages = Messages()

    status = AnthropicAdapter(client=EndedClient()).status("batch_1")
    assert status["state"] == "completed"


def test_openai_normalizes_responses_batch_line():
    adapter = OpenAIAdapter(client=object())
    result = adapter.normalize_line({
        "custom_id": "request_1",
        "response": {
            "status_code": 200,
            "body": {
                "model": "gpt-test-snapshot",
                "output": [{"content": [{"type": "output_text", "text": '{"results": []}'}]}],
                "usage": {"input_tokens": 12, "output_tokens": 4},
            },
        },
    })
    assert result.status == "succeeded"
    assert result.response_text == '{"results": []}'
    assert result.input_tokens == 12


def test_anthropic_normalizes_out_of_order_result_line():
    adapter = AnthropicAdapter(client=object())
    result = adapter.normalize_line({
        "custom_id": "request_2",
        "result": {
            "type": "succeeded",
            "message": {
                "model": "claude-test-snapshot",
                "content": [{"type": "text", "text": '{"results": []}'}],
                "usage": {"input_tokens": 10, "output_tokens": 3},
            },
        },
    })
    assert result.custom_id == "request_2"
    assert result.model == "claude-test-snapshot"
    assert result.output_tokens == 3


def test_openai_mocked_batch_lifecycle(tmp_path):
    class Files:
        def create(self, **kwargs):
            return SimpleNamespace(id="file_1")

        def content(self, file_id):
            return SimpleNamespace(content=b'{"custom_id":"request_1"}\n')

    class Batches:
        def create(self, **kwargs):
            return SimpleNamespace(id="batch_1", status="validating")

        def retrieve(self, batch_id):
            return SimpleNamespace(status="completed", output_file_id="out_1", error_file_id=None, model_dump=lambda: {"status": "completed"})

        def cancel(self, batch_id):
            return SimpleNamespace(model_dump=lambda: {"id": batch_id, "status": "cancelling"})

    adapter = OpenAIAdapter(client=SimpleNamespace(files=Files(), batches=Batches()))
    request = tmp_path / "requests.jsonl"
    request.write_text('{"custom_id":"request_1"}\n', encoding="utf-8")
    assert adapter.submit(request).batch_id == "batch_1"
    assert adapter.status("batch_1")["state"] == "completed"
    assert adapter.cancel("batch_1")["status"] == "cancelling"
    output, errors = tmp_path / "output.jsonl", tmp_path / "errors.jsonl"
    adapter.download("batch_1", output, errors)
    assert output.read_text(encoding="utf-8").startswith('{"custom_id"')


def test_anthropic_mocked_batch_lifecycle(tmp_path):
    class Batches:
        def create(self, requests):
            assert requests[0]["custom_id"] == "request_1"
            return SimpleNamespace(id="batch_1", processing_status="in_progress")

        def retrieve(self, batch_id):
            return {"processing_status": "ended", "request_counts": {"succeeded": 1}}

        def cancel(self, batch_id):
            return SimpleNamespace(model_dump=lambda: {"id": batch_id, "processing_status": "canceling"})

        def results(self, batch_id):
            yield {"custom_id": "request_1", "result": {"type": "expired"}}

    adapter = AnthropicAdapter(client=SimpleNamespace(messages=SimpleNamespace(batches=Batches())))
    request = tmp_path / "requests.jsonl"
    request.write_text(json.dumps({"custom_id": "request_1", "params": {}}) + "\n", encoding="utf-8")
    assert adapter.submit(request).batch_id == "batch_1"
    assert adapter.status("batch_1")["state"] == "completed"
    assert adapter.cancel("batch_1")["processing_status"] == "canceling"
    output, errors = tmp_path / "output.jsonl", tmp_path / "errors.jsonl"
    adapter.download("batch_1", output, errors)
    assert json.loads(output.read_text(encoding="utf-8"))["result"]["type"] == "expired"
