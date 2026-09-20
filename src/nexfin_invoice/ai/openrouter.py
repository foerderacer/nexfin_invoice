"""OpenRouter chat-completions client and the AI extraction orchestration.

Extraction contract:

* one JSON object, requested via ``response_format: json_schema`` (strict
  structured output, schema generated from :class:`InvoiceData`);
* if the model/API rejects structured output, retry once with a plain
  request and pull the JSON object out of the response text;
* on validation failure, retry once with the validator errors appended to
  the conversation; then raise :class:`AiError` with the model output
  attached.

The API key is never logged and never appears in error messages.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from ..errors import AiError
from ..model import InvoiceData, structured_output_schema
from .prompt import build_messages

__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_TIMEOUT",
    "OpenRouterClient",
    "ResponseFormatUnsupported",
    "extract_invoice_data",
]

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_TIMEOUT = 120.0

_MAX_ERROR_BODY = 500
_MAX_ECHO_OUTPUT = 2000

_RETRY_INSTRUCTION = (
    "The previous JSON output was rejected:\n{errors}\n"
    "Return the corrected JSON object only. Same schema, no prose."
)


class ResponseFormatUnsupported(Exception):
    """The model/API rejected ``response_format`` structured output."""


class OpenRouterClient:
    """Minimal synchronous OpenRouter chat-completions client."""

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.model = model
        self._client = httpx.Client(
            base_url=base_url,
            timeout=timeout,
            transport=transport,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "X-Title": "nexfin-invoice",
            },
        )

    def close(self) -> None:
        self._client.close()

    def chat(
        self, messages: list[dict[str, Any]], *, json_schema: dict[str, Any] | None = None
    ) -> str:
        """Send a chat completion request and return the assistant content."""
        payload: dict[str, Any] = {"model": self.model, "temperature": 0, "messages": messages}
        if json_schema is not None:
            payload["response_format"] = {"type": "json_schema", "json_schema": json_schema}
        try:
            response = self._client.post("/chat/completions", json=payload)
        except httpx.HTTPError as exc:
            raise AiError(f"OpenRouter request failed: {type(exc).__name__}: {exc}") from exc
        if response.status_code in (400, 404, 422) and _schema_rejected(response.text):
            raise ResponseFormatUnsupported(response.text[:_MAX_ERROR_BODY])
        if response.status_code != 200:
            raise AiError(
                f"OpenRouter API error {response.status_code}: {response.text[:_MAX_ERROR_BODY]}"
            )
        try:
            data = response.json()
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, ValueError) as exc:
            raise AiError(
                f"unexpected OpenRouter response shape: {response.text[:_MAX_ERROR_BODY]}"
            ) from exc
        if not isinstance(content, str) or not content.strip():
            raise AiError("model returned empty content")
        return content


def extract_invoice_data(
    client: OpenRouterClient,
    *,
    text: str | None = None,
    images: list[str] | None = None,
    categories: tuple[str, ...] | list[str] = (),
    accounts: tuple[str, ...] | list[str] = (),
) -> InvoiceData:
    """Extract invoice data via the AI, with the structured-output fallback
    and one validation-failure retry."""
    messages = build_messages(text=text, images=images, categories=categories, accounts=accounts)
    try:
        content = client.chat(messages, json_schema=structured_output_schema())
    except ResponseFormatUnsupported:
        content = client.chat(messages)
    return _validate_with_retry(client, messages, content)


def _validate_with_retry(
    client: OpenRouterClient,
    messages: list[dict[str, Any]],
    content: str,
) -> InvoiceData:
    invoice, errors = _try_validate(content)
    if invoice is not None:
        return invoice
    retry_messages = [
        *messages,
        {"role": "assistant", "content": content},
        {"role": "user", "content": _RETRY_INSTRUCTION.format(errors=errors)},
    ]
    retry_content = client.chat(retry_messages)
    invoice, retry_errors = _try_validate(retry_content)
    if invoice is not None:
        return invoice
    raise AiError(
        f"AI output failed validation even after retry ({retry_errors}). "
        f"Last model output: {retry_content[:_MAX_ECHO_OUTPUT]}"
    )


def _try_validate(content: str) -> tuple[InvoiceData | None, str]:
    data = _extract_json_object(content)
    if data is None:
        return None, "output was not a parseable JSON object"
    from pydantic import ValidationError

    try:
        return InvoiceData.model_validate(data), ""
    except ValidationError as exc:
        return None, _format_validation_errors(exc)


def _format_validation_errors(exc: Exception) -> str:
    from pydantic import ValidationError

    assert isinstance(exc, ValidationError)
    parts = []
    for error in exc.errors():
        loc = ".".join(str(item) for item in error.get("loc", ()))
        parts.append(f"{loc or '<root>'}: {error.get('msg')}")
    return "; ".join(parts)


def _extract_json_object(content: str) -> dict[str, Any] | None:
    """Pull a JSON object out of model output (plain, fenced, or embedded)."""
    text = content.strip()
    if text.startswith("```"):
        first_newline = text.find("\n")
        if first_newline != -1:
            text = text[first_newline + 1 :]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
        text = text.strip()
    try:
        parsed = json.loads(text)
    except ValueError:
        parsed = _scan_balanced_object(text)
    if isinstance(parsed, dict):
        return parsed
    return None


def _scan_balanced_object(text: str) -> Any:
    """Return the first balanced ``{...}`` block in ``text``, if any."""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : index + 1])
                except ValueError:
                    return None
    return None


def _schema_rejected(body: str) -> bool:
    lowered = body.lower()
    markers = ("response_format", "json_schema", "structured output")
    return any(marker in lowered for marker in markers)
