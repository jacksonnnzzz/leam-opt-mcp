import sys
import json
from types import SimpleNamespace

import pytest

import antenna_mcp.llm as llm_module
from antenna_mcp.llm import (
    DeepSeekChatProvider,
    OllamaVisionProvider,
    OpenAIResponsesProvider,
    RoutedLlmProvider,
    provider_from_env,
    vision_provider_from_env,
)


def test_multimodal_request_uses_original_image_and_high_detail_pdf(tmp_path, monkeypatch):
    image = tmp_path / "drawing.png"
    image.write_bytes(b"png")
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"pdf")
    captured = {}

    class Responses:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(output_text="ok")

    fake_client = SimpleNamespace(responses=Responses())
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=lambda: fake_client))
    monkeypatch.setenv("OPENAI_IMAGE_DETAIL", "original")

    result = OpenAIResponsesProvider("gpt-5.6-sol").generate(
        system="system",
        prompt="prompt",
        attachments=[image, pdf],
    )

    content = captured["input"][0]["content"]
    assert result == "ok"
    assert content[1]["type"] == "input_image"
    assert content[1]["detail"] == "original"
    assert content[2]["type"] == "input_file"
    assert content[2]["detail"] == "high"
    assert captured["reasoning"] == {"effort": "medium"}


def test_deepseek_uses_chat_completions_and_configured_endpoint(monkeypatch):
    captured = {}

    class Completions:
        def create(self, **kwargs):
            captured["request"] = kwargs
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="  result  "))]
            )

    def make_client(**kwargs):
        captured["client"] = kwargs
        return SimpleNamespace(chat=SimpleNamespace(completions=Completions()))

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=make_client))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-only")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")

    result = DeepSeekChatProvider("deepseek-v4-pro").generate(
        system="system", prompt="prompt", attachments=[]
    )

    assert result == "result"
    assert captured["client"] == {
        "api_key": "test-only",
        "base_url": "https://api.deepseek.com",
    }
    assert captured["request"]["model"] == "deepseek-v4-pro"
    assert captured["request"]["messages"][0] == {"role": "system", "content": "system"}


def test_deepseek_rejects_visual_attachments():
    provider = DeepSeekChatProvider()

    try:
        provider.generate(system="system", prompt="prompt", attachments=[object()])
    except ValueError as exc:
        assert "text-only" in str(exc)
    else:
        raise AssertionError("expected DeepSeek to reject visual attachments")


def test_router_sends_visual_attachments_only_to_vision_provider(tmp_path):
    calls = []

    class Provider:
        def __init__(self, name):
            self.name = name

        def generate(self, **kwargs):
            calls.append((self.name, kwargs["attachments"]))
            return self.name

    routed = RoutedLlmProvider(text=Provider("text"), vision=Provider("vision"))

    assert routed.generate(system="s", prompt="p", attachments=[]) == "text"
    attachment = tmp_path / "drawing.png"
    attachment.write_bytes(b"image")
    assert routed.generate(system="s", prompt="p", attachments=[attachment]) == "vision"
    assert calls == [("text", []), ("vision", [attachment])]


def test_router_inlines_json_attachment_for_text_provider(tmp_path):
    calls = []

    class Provider:
        def __init__(self, name):
            self.name = name

        def generate(self, **kwargs):
            calls.append((self.name, kwargs))
            return self.name

    benchmark = tmp_path / "benchmark.json"
    benchmark.write_text('{"generation_evidence":{"probe":"ground_to_signal"}}', "utf-8")
    routed = RoutedLlmProvider(text=Provider("text"), vision=Provider("vision"))

    assert routed.generate(system="s", prompt="p", attachments=[benchmark]) == "text"
    name, kwargs = calls[0]
    assert name == "text"
    assert kwargs["attachments"] == []
    assert "untrusted source evidence" in kwargs["prompt"]
    assert "BEGIN ATTACHMENT: benchmark.json" in kwargs["prompt"]
    assert "ground_to_signal" in kwargs["prompt"]


def test_router_keeps_mixed_text_and_visual_evidence_on_vision_provider(tmp_path):
    calls = []

    class Provider:
        def __init__(self, name):
            self.name = name

        def generate(self, **kwargs):
            calls.append((self.name, kwargs["attachments"]))
            return self.name

    benchmark = tmp_path / "benchmark.json"
    benchmark.write_text("{}", "utf-8")
    drawing = tmp_path / "drawing.png"
    drawing.write_bytes(b"image")
    attachments = [benchmark, drawing]
    routed = RoutedLlmProvider(text=Provider("text"), vision=Provider("vision"))

    assert routed.generate(system="s", prompt="p", attachments=attachments) == "vision"
    assert calls == [("vision", attachments)]


def test_router_rejects_oversized_text_attachment(tmp_path, monkeypatch):
    benchmark = tmp_path / "benchmark.json"
    benchmark.write_text('{"too_long":true}', "utf-8")
    monkeypatch.setenv("ANTENNA_TEXT_ATTACHMENT_MAX_CHARS", "5")
    routed = RoutedLlmProvider(text=object(), vision=object())

    with pytest.raises(ValueError, match="ANTENNA_TEXT_ATTACHMENT_MAX_CHARS=5"):
        routed.generate(system="s", prompt="p", attachments=[benchmark])


def test_text_model_env_overrides_persisted_job_model_without_changing_ollama(monkeypatch):
    monkeypatch.setenv("ANTENNA_TEXT_PROVIDER", "deepseek")
    monkeypatch.setenv("ANTENNA_VISION_PROVIDER", "ollama")
    monkeypatch.setenv("ANTENNA_TEXT_MODEL", "  deepseek-v4-pro  ")
    monkeypatch.setenv("OLLAMA_VISION_MODEL", "qwen3-vl:8b")

    provider = provider_from_env("qwen3-vl:8b")

    assert isinstance(provider, RoutedLlmProvider)
    assert isinstance(provider.text, DeepSeekChatProvider)
    assert provider.text.model == "deepseek-v4-pro"
    assert isinstance(provider.vision, OllamaVisionProvider)
    assert provider.vision.model == "qwen3-vl:8b"


def test_blank_text_model_env_preserves_persisted_model(monkeypatch):
    monkeypatch.setenv("ANTENNA_TEXT_PROVIDER", "deepseek")
    monkeypatch.setenv("ANTENNA_VISION_PROVIDER", "ollama")
    monkeypatch.setenv("ANTENNA_TEXT_MODEL", "   ")
    monkeypatch.setenv("OLLAMA_VISION_MODEL", "vision-only-model")

    provider = provider_from_env("persisted-text-model")

    assert provider.text.model == "persisted-text-model"
    assert provider.vision.model == "vision-only-model"


def test_ollama_vision_sends_local_image_as_base64(tmp_path, monkeypatch):
    image = tmp_path / "drawing.png"
    image.write_bytes(b"image-bytes")
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({"message": {"content": "  {\"ok\": true}  "}}).encode()

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["payload"] = json.loads(request.data)
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr(llm_module, "urlopen", fake_urlopen)
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")
    monkeypatch.setenv("OLLAMA_TIMEOUT_SECONDS", "10")

    result = OllamaVisionProvider("qwen3-vl:8b").generate(
        system="system", prompt="analyze", attachments=[image]
    )

    assert result == '{"ok": true}'
    assert captured["url"] == "http://localhost:11434/api/chat"
    assert captured["timeout"] == 10
    assert captured["payload"]["model"] == "qwen3-vl:8b"
    assert captured["payload"]["format"] == "json"
    assert captured["payload"]["think"] is False
    assert captured["payload"]["options"]["num_ctx"] == 16384
    assert captured["payload"]["messages"][1]["images"] == ["aW1hZ2UtYnl0ZXM="]


def test_ollama_supports_text_only_generation(monkeypatch):
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({"message": {"content": '{"ok":true}'}}).encode()

    def fake_urlopen(request, timeout):
        captured["payload"] = json.loads(request.data)
        return Response()

    monkeypatch.setattr(llm_module, "urlopen", fake_urlopen)
    result = OllamaVisionProvider("qwen3-vl:8b").generate(
        system="system", prompt="generate parameters", attachments=[]
    )

    assert result == '{"ok":true}'
    message = captured["payload"]["messages"][1]
    assert message["content"] == "generate parameters"
    assert "images" not in message


def test_ollama_does_not_force_json_grammar_for_python_stage(monkeypatch):
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps(
                {"message": {"content": 'patch = hfss.modeler.create_box([0, 0, 0], [1, 1, 1])'}}
            ).encode()

    def fake_urlopen(request, timeout):
        captured["payload"] = json.loads(request.data)
        return Response()

    monkeypatch.setattr(llm_module, "urlopen", fake_urlopen)
    result = OllamaVisionProvider("qwen3-vl:8b").generate(
        system="system",
        prompt="Template: paper_reconstruction\nStage: model_3d\nOutput contract:\nReturn Python.",
        attachments=[],
    )

    assert result.startswith("patch = hfss.modeler.create_box")
    assert "format" not in captured["payload"]


def test_ollama_vision_accepts_only_json_thinking_fallback(tmp_path, monkeypatch):
    image = tmp_path / "drawing.png"
    image.write_bytes(b"image-bytes")

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps(
                {"message": {"content": "", "thinking": '{"summary":"patch"}'}, "done_reason": "stop"}
            ).encode()

    monkeypatch.setattr(llm_module, "urlopen", lambda *args, **kwargs: Response())

    result = OllamaVisionProvider("qwen3-vl:8b").generate(
        system="system", prompt="analyze", attachments=[image]
    )

    assert result == '{"summary":"patch"}'


def test_visual_only_ollama_pdf_audit_does_not_append_extracted_text(tmp_path, monkeypatch):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"pdf")
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({"message": {"content": '{"ok":true}'}}).encode()

    def fake_urlopen(request, timeout):
        captured["payload"] = json.loads(request.data)
        return Response()

    monkeypatch.setattr(llm_module, "_render_pdf_pages", lambda path: [b"page-image"])
    monkeypatch.setattr(
        llm_module,
        "_extract_pdf_text",
        lambda path: (_ for _ in ()).throw(AssertionError("PDF text must not be extracted")),
    )
    monkeypatch.setattr(llm_module, "urlopen", fake_urlopen)
    monkeypatch.setenv("ANTENNA_VISION_PROVIDER", "ollama")

    provider = vision_provider_from_env(visual_only=True)
    result = provider.generate(system="system", prompt="visual evidence only", attachments=[pdf])

    assert result == '{"ok":true}'
    message = captured["payload"]["messages"][1]
    assert "locally extracted PDF text" not in message["content"]
    assert message["images"]
