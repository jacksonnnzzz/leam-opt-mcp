from __future__ import annotations

import base64
import json
import mimetypes
import os
from pathlib import Path
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class LlmProvider(Protocol):
    def generate(self, *, system: str, prompt: str, attachments: list[Path]) -> str: ...


class OpenAIResponsesProvider:
    """Small Responses API adapter; imported lazily so offline tests need no SDK."""

    def __init__(self, model: str | None = None) -> None:
        self.model = model or os.getenv("OPENAI_MODEL", "gpt-5.6-sol")
        self.vision_model = os.getenv("OPENAI_VISION_MODEL", self.model)

    def generate(self, *, system: str, prompt: str, attachments: list[Path]) -> str:
        from openai import OpenAI

        content: list[dict[str, str]] = [{"type": "input_text", "text": prompt}]
        image_detail = os.getenv("OPENAI_IMAGE_DETAIL", "original").lower()
        if image_detail not in {"low", "high", "auto", "original"}:
            raise ValueError("OPENAI_IMAGE_DETAIL must be low, high, auto, or original")
        for path in attachments:
            mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            if mime.startswith("image/"):
                content.append(
                    {
                        "type": "input_image",
                        "image_url": f"data:{mime};base64,{encoded}",
                        "detail": image_detail,
                    }
                )
            elif mime == "application/pdf":
                content.append(
                    {
                        "type": "input_file",
                        "filename": path.name,
                        "file_data": f"data:{mime};base64,{encoded}",
                        "detail": "high",
                    }
                )
            else:
                content.append({"type": "input_text", "text": f"Attachment {path.name}:\n{path.read_text('utf-8')}"})
        selected_model = self.vision_model if attachments else self.model
        request = dict(
            model=selected_model,
            instructions=system,
            input=[{"role": "user", "content": content}],
        )
        # GPT-4o is useful as a vision fallback but does not accept the reasoning
        # configuration used by o-series and GPT-5-family models.
        if not selected_model.lower().startswith("gpt-4o"):
            request["reasoning"] = {"effort": "medium"}
        response = OpenAI().responses.create(**request)
        return response.output_text.strip()


class DeepSeekChatProvider:
    """Text-only DeepSeek adapter using its OpenAI-compatible Chat Completions API."""

    def __init__(self, model: str | None = None) -> None:
        self.model = model or os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro")
        self.base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")

    def generate(self, *, system: str, prompt: str, attachments: list[Path]) -> str:
        if attachments:
            raise ValueError(
                "DeepSeek's current API model is text-only and cannot analyze image/PDF attachments. "
                "Configure ANTENNA_VISION_PROVIDER=openai with a separate multimodal API key."
            )
        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            raise RuntimeError("DEEPSEEK_API_KEY is not configured in this process")

        from openai import OpenAI

        client = OpenAI(api_key=api_key, base_url=self.base_url)
        response = client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        )
        content = response.choices[0].message.content
        if not content:
            raise RuntimeError("DeepSeek returned an empty text response")
        return content.strip()


class RoutedLlmProvider:
    """Route attachment analysis to vision and later text stages to reasoning."""

    def __init__(self, *, text: LlmProvider, vision: LlmProvider) -> None:
        self.text = text
        self.vision = vision

    def generate(self, *, system: str, prompt: str, attachments: list[Path]) -> str:
        provider = self.vision if attachments else self.text
        return provider.generate(system=system, prompt=prompt, attachments=attachments)


class OllamaVisionProvider:
    """Local image/PDF analysis through Ollama's native vision API."""

    def __init__(self, model: str | None = None, *, include_pdf_text: bool = True) -> None:
        self.model = model or os.getenv("OLLAMA_VISION_MODEL", "qwen3-vl:8b")
        self.base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
        self.include_pdf_text = include_pdf_text

    def generate(self, *, system: str, prompt: str, attachments: list[Path]) -> str:
        if not attachments:
            raise ValueError("OllamaVisionProvider requires at least one image, PDF, or text attachment")

        images: list[str] = []
        labels: list[str] = []
        text_attachments: list[str] = []
        for path in attachments:
            mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            if mime.startswith("image/"):
                images.append(base64.b64encode(path.read_bytes()).decode("ascii"))
                labels.append(path.name)
            elif mime == "application/pdf":
                pages = _render_pdf_pages(path)
                images.extend(base64.b64encode(page).decode("ascii") for page in pages)
                labels.extend(f"{path.name} page {index}" for index in range(1, len(pages) + 1))
                if self.include_pdf_text:
                    text_attachments.append(
                        f"[{path.name} locally extracted PDF text]\n{_extract_pdf_text(path)}"
                    )
            elif mime.startswith("text/") or path.suffix.lower() in {".md", ".json", ".csv"}:
                text_attachments.append(f"[{path.name}]\n{path.read_text('utf-8')}")
            else:
                raise ValueError(f"unsupported local-vision attachment type: {path.name} ({mime})")

        label_text = ", ".join(labels) or "no image pages"
        user_prompt = f"{prompt}\n\nImage order: {label_text}."
        if text_attachments:
            user_prompt += "\n\nText attachments:\n" + "\n\n".join(text_attachments)
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_prompt, "images": images},
            ],
            "stream": False,
            "think": False,
            "format": "json",
            "options": {
                "temperature": 0,
                "num_ctx": int(os.getenv("OLLAMA_NUM_CTX", "16384")),
            },
        }
        request = Request(
            f"{self.base_url}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        timeout = float(os.getenv("OLLAMA_TIMEOUT_SECONDS", "900"))
        try:
            with urlopen(request, timeout=timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"Ollama returned HTTP {exc.code}: {detail or exc.reason}") from exc
        except URLError as exc:
            raise RuntimeError(
                f"cannot reach Ollama at {self.base_url}; install/start Ollama and pull {self.model}"
            ) from exc
        message = data.get("message", {})
        content = message.get("content")
        # Some thinking-capable Ollama vision templates place a schema-constrained
        # final JSON object in ``message.thinking`` while leaving content empty,
        # even when think=false. Accept it only when the whole field is valid JSON;
        # never persist free-form chain-of-thought as a modeling artifact.
        if not content:
            structured_fallback = message.get("thinking") or ""
            try:
                json.loads(structured_fallback)
            except (TypeError, json.JSONDecodeError):
                pass
            else:
                content = structured_fallback
        if not content:
            error = data.get("error") or data.get("done_reason") or "empty response"
            raise RuntimeError(f"Ollama vision request failed: {error}")
        return content.strip()


def provider_from_env(model: str | None = None) -> LlmProvider:
    """Build the configured split provider without putting credentials in job state."""

    text_name = os.getenv("ANTENNA_TEXT_PROVIDER", "openai").strip().lower()
    vision_name = os.getenv("ANTENNA_VISION_PROVIDER", "openai").strip().lower()

    if text_name == "openai" and vision_name == "openai":
        provider = OpenAIResponsesProvider(model)
        return RoutedLlmProvider(text=provider, vision=provider)

    text = _provider(text_name, model=model)
    vision = _provider(vision_name)
    return RoutedLlmProvider(text=text, vision=vision)


def vision_provider_from_env(model: str | None = None, *, visual_only: bool = False) -> LlmProvider:
    """Build only the configured vision provider for independent evidence checks."""

    name = os.getenv("ANTENNA_VISION_PROVIDER", "openai").strip().lower()
    return _provider(name, model=model, visual_only=visual_only)


def _provider(
    name: str,
    *,
    model: str | None = None,
    visual_only: bool = False,
) -> LlmProvider:
    if name == "openai":
        return OpenAIResponsesProvider(model)
    if name == "deepseek":
        return DeepSeekChatProvider(model)
    if name == "ollama":
        return OllamaVisionProvider(model, include_pdf_text=not visual_only)
    raise ValueError(f"unsupported LLM provider: {name!r}; expected 'openai', 'deepseek', or 'ollama'")


def _render_pdf_pages(path: Path) -> list[bytes]:
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError(
            "PDF input with local vision requires PyMuPDF; install the local-vision project extra"
        ) from exc

    dpi = int(os.getenv("OLLAMA_PDF_DPI", "144"))
    max_pages = int(os.getenv("OLLAMA_PDF_MAX_PAGES", "40"))
    if not 72 <= dpi <= 300:
        raise ValueError("OLLAMA_PDF_DPI must be between 72 and 300")
    with fitz.open(path) as document:
        if document.page_count > max_pages:
            raise ValueError(
                f"{path.name} has {document.page_count} pages, exceeding OLLAMA_PDF_MAX_PAGES={max_pages}"
            )
        matrix = fitz.Matrix(dpi / 72, dpi / 72)
        return [
            document.load_page(index).get_pixmap(matrix=matrix, alpha=False).tobytes("png")
            for index in range(document.page_count)
        ]


def _extract_pdf_text(path: Path) -> str:
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError(
            "PDF input with local vision requires PyMuPDF; install the local-vision project extra"
        ) from exc

    max_chars = int(os.getenv("OLLAMA_PDF_TEXT_MAX_CHARS", "120000"))
    with fitz.open(path) as document:
        sections = [
            f"--- page {index + 1} ---\n{document.load_page(index).get_text('text')}"
            for index in range(document.page_count)
        ]
    text = "\n\n".join(sections)
    if len(text) > max_chars:
        raise ValueError(
            f"extracted PDF text has {len(text)} characters, exceeding "
            f"OLLAMA_PDF_TEXT_MAX_CHARS={max_chars}"
        )
    return text
