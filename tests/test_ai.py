"""Tests for the OpenRouter client and AI extraction orchestration (mocked)."""

from __future__ import annotations

import warnings
from decimal import Decimal

import httpx
import pytest

from conftest import completion, invoice_json, make_mock_client
from nexfin_invoice.ai.openrouter import (
    OpenRouterClient,
    ResponseFormatUnsupported,
    extract_invoice_data,
)
from nexfin_invoice.errors import AiError
from nexfin_invoice.model import InvoiceData


def ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json=completion(invoice_json()))


def extract_logging_warnings(
    client: OpenRouterClient,
) -> tuple[InvoiceData, list[warnings.WarningMessage]]:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        invoice = extract_invoice_data(client, text="x")
    return invoice, [entry for entry in caught if issubclass(entry.category, UserWarning)]


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
    assert "parsed_by" not in schema["schema"]["properties"]
    messages = payload["messages"]
    assert messages[0]["role"] == "system"
    user_content = messages[1]["content"]
    assert user_content[0]["type"] == "text"
    assert "RE-2026-0777" in user_content[0]["text"]


def test_schema_includes_skonto_fields_and_stays_strict() -> None:
    from nexfin_invoice.model import structured_output_schema

    schema = structured_output_schema()
    inner = schema["schema"]
    properties = inner["properties"]
    assert "due_skonto" in properties
    assert "amount_skonto" in properties
    assert "parsed_by" not in properties
    assert schema["strict"] is True
    assert inner["additionalProperties"] is False
    assert inner["required"] == list(properties)


def test_hallucinated_parsed_by_is_dropped() -> None:
    client, _ = make_mock_client(
        lambda request: httpx.Response(200, json=completion(invoice_json(parsed_by="zugferd")))
    )
    invoice = extract_invoice_data(client, text="x")
    assert invoice.id == "RE-2026-0777"
    assert invoice.parsed_by is None


def test_prompt_mentions_skonto_rules() -> None:
    from nexfin_invoice.ai.prompt import build_system_prompt

    prompt = build_system_prompt()
    assert "due_skonto" in prompt
    assert "amount_skonto" in prompt
    assert "together" in prompt


def test_model_answer_with_skonto_validates() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=completion(
                invoice_json(due_skonto="2026-09-10", amount_skonto=-99.75)
            ),
        )

    client, log = make_mock_client(handler)
    invoice = extract_invoice_data(client, text="x")
    assert str(invoice.due_skonto) == "2026-09-10"
    assert invoice.amount_skonto == Decimal("-99.75")

    # The schema payload still demands every property, nulls included.
    sent_schema = log.payloads[0]["response_format"]["json_schema"]["schema"]
    assert "due_skonto" in sent_schema["properties"]


def test_partial_skonto_pair_recovers_via_retry() -> None:
    responses = iter(
        [
            invoice_json(due_skonto="2026-09-10"),  # partial pair → ValidationError
            invoice_json(due_skonto="2026-09-10", amount_skonto=-99.75),
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=completion(next(responses)))

    client, log = make_mock_client(handler)
    invoice = extract_invoice_data(client, text="x")
    assert str(invoice.due_skonto) == "2026-09-10"
    assert invoice.amount_skonto == Decimal("-99.75")
    retry_messages = log.payloads[1]["messages"]
    assert len(retry_messages) == 4  # system, user, assistant, user
    assert "together" in retry_messages[3]["content"]


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


# -- IBAN recheck -----------------------------------------------------------


def test_invalid_iban_triggers_one_corrective_redo() -> None:
    responses = iter(
        [invoice_json(iban="DE89370400440532013001"), invoice_json(iban="DE89370400440532013000")]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=completion(next(responses)))

    client, log = make_mock_client(handler)
    invoice, warned = extract_logging_warnings(client)
    assert invoice.iban == "DE89370400440532013000"
    assert len(log.payloads) == 2
    retry_messages = log.payloads[1]["messages"]
    assert len(retry_messages) == 4  # system, user, assistant, user
    assert retry_messages[2]["content"] == invoice_json(iban="DE89370400440532013001")
    assert "mod-97" in retry_messages[3]["content"]
    assert "DE89370400440532013001" in retry_messages[3]["content"]
    assert "response_format" not in log.payloads[1]
    assert warned == []


def test_redo_result_wins_even_when_iban_is_still_invalid() -> None:
    responses = iter(
        [
            invoice_json(iban="DE89370400440532013001"),
            invoice_json(iban="DE89370400440532013001", reference="redone"),
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=completion(next(responses)))

    client, log = make_mock_client(handler)
    invoice, warned = extract_logging_warnings(client)
    assert len(log.payloads) == 2
    assert invoice.reference == "redone"
    assert invoice.iban == "DE89370400440532013001"
    assert len(warned) == 1
    assert "mod-97" in str(warned[0].message)


def test_valid_iban_never_redoes() -> None:
    client, log = make_mock_client(ok)
    invoice, warned = extract_logging_warnings(client)
    assert invoice.iban == "DE89370400440532013000"
    assert len(log.payloads) == 1
    assert warned == []


def test_null_iban_never_redoes() -> None:
    client, log = make_mock_client(
        lambda request: httpx.Response(200, json=completion(invoice_json(iban=None)))
    )
    invoice, warned = extract_logging_warnings(client)
    assert invoice.iban is None
    assert len(log.payloads) == 1
    assert warned == []


def test_schema_retry_and_iban_redo_have_independent_budgets() -> None:
    responses = iter(
        [
            invoice_json(amount=-105.911),
            invoice_json(iban="DE89370400440532013001"),
            invoice_json(),
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=completion(next(responses)))

    client, log = make_mock_client(handler)
    invoice, warned = extract_logging_warnings(client)
    assert invoice.iban == "DE89370400440532013000"
    assert len(log.payloads) == 3
    assert warned == []
    redo_messages = log.payloads[2]["messages"]
    assert len(redo_messages) == 4  # system, user, assistant, user
    assert redo_messages[2]["content"] == invoice_json(iban="DE89370400440532013001")
    assert "mod-97" in redo_messages[3]["content"]


def test_redo_garbage_keeps_original_invoice() -> None:
    responses = iter(
        [
            invoice_json(iban="DE89370400440532013001", reference="original"),
            "I am sorry, I cannot help with that.",
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=completion(next(responses)))

    client, log = make_mock_client(handler)
    invoice, warned = extract_logging_warnings(client)
    assert len(log.payloads) == 2
    assert invoice.reference == "original"
    assert invoice.iban == "DE89370400440532013001"
    assert len(warned) == 1


def test_redo_api_failure_keeps_original_invoice() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(
                200,
                json=completion(invoice_json(iban="DE89370400440532013001", reference="original")),
            )
        return httpx.Response(503, text="overloaded")

    client, log = make_mock_client(handler)
    invoice, warned = extract_logging_warnings(client)
    assert len(log.payloads) == 2
    assert invoice.reference == "original"
    assert invoice.iban == "DE89370400440532013001"
    assert len(warned) == 1


def test_redo_discarded_attempt_warnings_do_not_leak() -> None:
    responses = iter(
        [
            invoice_json(
                iban="DE89370400440532013001", due_skonto="2026-09-10", amount_skonto=-200.0
            ),
            invoice_json(due_skonto="2026-09-10", amount_skonto=-150.0),
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=completion(next(responses)))

    client, log = make_mock_client(handler)
    invoice, warned = extract_logging_warnings(client)
    assert len(log.payloads) == 2
    assert invoice.amount_skonto == Decimal("-150.00")
    assert len(warned) == 1
    assert "amount_skonto -150.0" in str(warned[0].message)
    assert "-200" not in str(warned[0].message)


def test_redo_may_null_the_iban() -> None:
    responses = iter([invoice_json(iban="DE89370400440532013001"), invoice_json(iban=None)])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=completion(next(responses)))

    client, log = make_mock_client(handler)
    invoice, warned = extract_logging_warnings(client)
    assert len(log.payloads) == 2
    assert invoice.iban is None
    assert warned == []
