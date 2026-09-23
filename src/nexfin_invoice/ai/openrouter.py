"""OpenRouter chat-completions client and the AI extraction orchestration.

Extraction contract:

* one JSON object, requested via ``response_format: json_schema`` (strict
  structured output, schema generated from :class:`InvoiceData`);
* if the model/API rejects structured output, retry once with a plain
  request and pull the JSON object out of the response text;
* on validation failure, retry once with the validator errors appended to
  the conversation; then raise :class:`AiError` with the model output
  attached;
* if the extracted IBAN is present but invalid (format / ISO 7064 mod-97),
  redo the extraction once with corrective feedback and accept the
  corrected output; an unusable or failed redo keeps the invoice
  warn-only, mirroring the nexfin pay dialog.

The API key is never logged and never appears in error messages.
"""

from __future__ import annotations

import json
import warnings
from typing import Any

import httpx

from ..errors import AiError
from ..model import InvoiceData, iban_problem, structured_output_schema
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

_IBAN_RETRY_INSTRUCTION = (
    "The extracted IBAN {iban} is invalid because {problem}.\n"
    "Re-read the payment IBAN from the document carefully (OCR confusions like "
    "0/O and 1/I/l are common), set `iban` to null when the document has no IBAN, "
    "and return the full corrected JSON object only. Same schema, no prose."
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
    """Extract invoice data via the AI, with the structured-output fallback,
    one validation-failure retry and one corrective IBAN redo.

    Warnings raised while constructing discarded attempts are dropped and
    the winning invoice's warnings are re-emitted exactly once, so a
    superseded attempt never produces spurious diagnostics.
    """
    messages = build_messages(text=text, images=images, categories=categories, accounts=accounts)
    try:
        content = client.chat(messages, json_schema=structured_output_schema())
    except ResponseFormatUnsupported:
        content = client.chat(messages)
    invoice, winning_content, caught = _validate_with_retry(client, messages, content)
    invoice, caught = _retry_bad_iban(client, messages, winning_content, invoice, caught)
    for entry in caught:
        warnings.warn_explicit(entry.message, entry.category, entry.filename, entry.lineno)
    return invoice


def _validate_with_retry(
    client: OpenRouterClient,
    messages: list[dict[str, Any]],
    content: str,
) -> tuple[InvoiceData, str, list[warnings.WarningMessage]]:
    """Return the validated invoice, the model content it was built from,
    and the warnings recorded while constructing it."""
    invoice, errors, caught = _try_validate(content)
    if invoice is not None:
        return invoice, content, caught
    retry_messages = [
        *messages,
        {"role": "assistant", "content": content},
        {"role": "user", "content": _RETRY_INSTRUCTION.format(errors=errors)},
    ]
    retry_content = client.chat(retry_messages)
    invoice, retry_errors, retry_caught = _try_validate(retry_content)
    if invoice is not None:
        return invoice, retry_content, retry_caught
    raise AiError(
        f"AI output failed validation even after retry ({retry_errors}). "
        f"Last model output: {retry_content[:_MAX_ECHO_OUTPUT]}"
    )


def _retry_bad_iban(
    client: OpenRouterClient,
    messages: list[dict[str, Any]],
    content: str,
    invoice: InvoiceData,
    caught: list[warnings.WarningMessage],
) -> tuple[InvoiceData, list[warnings.WarningMessage]]:
    """One corrective redo when the extracted IBAN is present but invalid.

    A schema-valid redo answer wins outright (even with a still-invalid or
    now-null IBAN); an unusable or failed redo keeps the original invoice
    with its warnings. Never raises: an invalid IBAN is warn-only, never a
    booking blocker.
    """
    if invoice.iban is None:
        return invoice, caught
    problem = iban_problem(invoice.iban)
    if problem is None:
        return invoice, caught
    retry_messages = [
        *messages,
        {"role": "assistant", "content": content},
        {
            "role": "user",
            "content": _IBAN_RETRY_INSTRUCTION.format(iban=invoice.iban, problem=problem),
        },
    ]
    try:
        retry_content = client.chat(retry_messages)
    except (AiError, ResponseFormatUnsupported):
        return invoice, caught
    corrected, _, corrected_caught = _try_validate(retry_content)
    if corrected is None:
        return invoice, caught
    return corrected, corrected_caught


def _try_validate(
    content: str,
) -> tuple[InvoiceData | None, str, list[warnings.WarningMessage]]:
    data = _extract_json_object(content)
    if data is None:
        return None, "output was not a parseable JSON object", []
    # Free-form fallback (ResponseFormatUnsupported) can hallucinate the
    # converter-only key; extra="forbid" would burn the retry for no reason.
    data.pop("parsed_by", None)
    from pydantic import ValidationError

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            invoice: InvoiceData | None = InvoiceData.model_validate(data)
            errors = ""
        except ValidationError as exc:
            invoice = None
            errors = _format_validation_errors(exc)
    return invoice, errors, caught


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
