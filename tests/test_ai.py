"""Tests for the OpenRouter client and AI extraction orchestration (mocked)."""

from __future__ import annotations

from decimal import Decimal

import httpx
import pytest

from conftest import completion, invoice_json, make_mock_client
from nexfin_invoice.ai.openrouter import ResponseFormatUnsupported, extract_invoice_data
from nexfin_invoice.errors import AiError
from nexfin_invoice.model import InvoiceData


def ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json=completion(invoice_json()))


def test_structured_output_request_and_success() -> None:
    client, log = make_mock_client(ok)
    invoice = extract_invoice_data(client, text="Invoice RE-2026-0777 from Muller GmbH")
    assert isinstance(invoice, InvoiceData)
    assert invoice.id == "RE-2026-0777"
    assert invoice.amount == Decimal("-105.91")

    payload = log.payloads[0]
    assert payload["temperature"] == 0
    assert payload["response_format"]["type"] == "json_schema"
    schema = payload["response_format"]["json_schema"]
    assert schema["strict"] is True
    assert schema["schema"]["additionalProperties"] is False
    messages = payload["messages"]
    assert messages[0]["role"] == "system"
    user_content = messages[1]["content"]
    assert user_content[0]["type"] == "text"
    assert "RE-2026-0777" in user_content[0]["text"]


def test_categories_and_accounts_injected() -> None:
    client, log = make_mock_client(ok)
    extract_invoice_data(client, text="x", categories=("office", "travel"), accounts=("Checking",))
    system = log.payloads[0]["messages"][0]["content"]
    assert "MUST be one of: office, travel" in system
    assert "MUST be one of: Checking" in system


def test_categories_free_when_not_configured() -> None:
    client, log = make_mock_client(ok)
    extract_invoice_data(client, text="x")
    system = log.payloads[0]["messages"][0]["content"]
    assert "label of your choice" in system


def test_response_format_rejection_falls_back_to_plain() -> None:
    calls: list[bool] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.content.decode()
        calls.append("response_format" in body)
        if len(calls) == 1:
            assert "response_format" in body
            return httpx.Response(
                400, json={"error": {"message": "response_format is not supported by this model"}}
            )
        assert "response_format" not in body
        return httpx.Response(200, json=completion("```json\n" + invoice_json() + "\n```"))

    client, log = make_mock_client(handler)
    invoice = extract_invoice_data(client, text="x")
    assert invoice.id == "RE-2026-0777"
    assert calls == [True, False]
    assert len(log.payloads) == 2


def test_validation_failure_retries_once_with_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=completion(invoice_json(amount=-105.911)))

    client, log = make_mock_client(handler)
    with pytest.raises(AiError, match="validation"):
        extract_invoice_data(client, text="x")

    assert len(log.payloads) == 2
    retry_messages = log.payloads[1]["messages"]
    assert len(retry_messages) == 4  # system, user, assistant, user
    assert retry_messages[2]["role"] == "assistant"
    assert "decimal" in retry_messages[3]["content"]


def test_retry_can_recover() -> None:
    responses = iter([invoice_json(id=""), invoice_json()])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=completion(next(responses)))

    client, _ = make_mock_client(handler)
    invoice = extract_invoice_data(client, text="x")
    assert invoice.id == "RE-2026-0777"


def test_garbage_output_raises_with_output_attached() -> None:
    client, _ = make_mock_client(
        lambda request: httpx.Response(200, json=completion("I am sorry, I cannot help with that."))
    )
    with pytest.raises(AiError, match="I am sorry"):
        extract_invoice_data(client, text="x")


def test_api_error_raises() -> None:
    client, _ = make_mock_client(lambda request: httpx.Response(500, text="boom"))
    with pytest.raises(AiError, match="500"):
        extract_invoice_data(client, text="x")


def test_empty_content_raises() -> None:
    client, _ = make_mock_client(
        lambda request: httpx.Response(200, json=completion("   "))
    )
    with pytest.raises(AiError, match="empty"):
        extract_invoice_data(client, text="x")


def test_embedded_json_with_prose_is_extracted() -> None:
    prose = "Here is the JSON you requested:\n" + invoice_json() + "\nRegards, model."
    client, _ = make_mock_client(lambda request: httpx.Response(200, json=completion(prose)))
    invoice = extract_invoice_data(client, text="x")
    assert invoice.vendor == "Muller GmbH"


def test_images_are_sent_as_data_urls() -> None:
    client, log = make_mock_client(ok)
    extract_invoice_data(client, images=["data:image/png;base64,QUJD"])
    user_content = log.payloads[0]["messages"][1]["content"]
    assert any(part["type"] == "image_url" for part in user_content)
    assert user_content[0]["text"].startswith("Extract the invoice data")


def test_no_input_raises_value_error() -> None:
    client, _ = make_mock_client(ok)
    with pytest.raises(ValueError):
        extract_invoice_data(client)


def test_schema_rejection_detector() -> None:
    from nexfin_invoice.ai.openrouter import _schema_rejected

    assert _schema_rejected('{"error":{"message":"response_format unsupported"}}')
    assert _schema_rejected("json_schema not allowed")
    assert not _schema_rejected('{"error":{"message":"invalid api key"}}')


def test_response_format_unsupported_is_importable_error() -> None:
    assert issubclass(ResponseFormatUnsupported, Exception)
