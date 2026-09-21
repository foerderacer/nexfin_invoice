"""Golden tests for the front-matter writer, matched against the shape of
the example in docs/invoice-format.md."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from nexfin_invoice.model import InvoiceData
from nexfin_invoice.render import output_filename, render, unique_output_path, write_markdown


def sample_invoice(**overrides: object) -> InvoiceData:
    data: dict = {
        "id": "RE-2026-0912",
        "vendor": "Muller GmbH",
        "issued": "2026-09-01",
        "due": "2026-09-15",
        "amount": "-119.00",
        "currency": "EUR",
        "category": "office",
        "account": "Checking",
        "iban": "DE89370400440532013000",
        "account_holder": "Muller GmbH",
        "reference": "RE-2026-0912",
        "line_items": [
            {"description": "Widget", "quantity": "2", "amount": "45.00"},
            {"description": "Shipping", "amount": "29.00"},
        ],
    }
    data.update(overrides)
    return InvoiceData(**data)


GOLDEN = """---
id: RE-2026-0912
vendor: Muller GmbH
issued: 2026-09-01
due: 2026-09-15
amount: -119.00
currency: EUR
category: office
account: Checking
iban: DE89370400440532013000
account_holder: Muller GmbH
reference: RE-2026-0912
status: open
booked: ""
paid_date: ""
---

# RE-2026-0912 — Muller GmbH

## Line items

- 2x Widget ... 45.00
- Shipping ... 29.00
"""


def test_golden_matches_documented_shape() -> None:
    assert render(sample_invoice()) == GOLDEN


SKONTO_GOLDEN = """---
id: RE-2026-0912
vendor: Muller GmbH
issued: 2026-09-01
due_skonto: 2026-09-10
due: 2026-09-15
amount_skonto: -110.00
amount: -119.00
currency: EUR
status: open
booked: ""
paid_date: ""
---

# RE-2026-0912 — Muller GmbH
"""


def test_skonto_fields_rendered_in_document_order() -> None:
    invoice = sample_invoice(
        category=None,
        account=None,
        iban=None,
        account_holder=None,
        reference=None,
        line_items=[],
        due_skonto="2026-09-10",
        amount_skonto="-110.00",
    )
    text = render(invoice)
    assert text == SKONTO_GOLDEN
    assert "due_skonto: 2026-09-10" in text
    assert "amount_skonto: -110.00" in text


def test_skonto_amount_rendered_raw_not_quoted() -> None:
    text = render(sample_invoice(due_skonto="2026-09-10", amount_skonto="-110.00"))
    assert "amount_skonto: -110.00" in text
    assert '"-110.00"' not in text


def test_skonto_zero_renders_raw() -> None:
    text = render(sample_invoice(due_skonto="2026-09-10", amount_skonto=0))
    assert "amount_skonto: 0.00" in text
    assert 'amount_skonto: "0.00"' not in text


def test_skonto_omitted_when_absent() -> None:
    text = render(sample_invoice())
    assert "skonto" not in text


def test_amount_always_two_decimals() -> None:
    text = render(sample_invoice(amount=Decimal("2500")))
    assert "amount: 2500.00" in text
    text = render(sample_invoice(amount=Decimal("-119.0")))
    assert "amount: -119.00" in text


def test_optional_fields_omitted_when_empty() -> None:
    text = render(
        sample_invoice(
            category=None, account=None, iban=None, account_holder=None, reference=None
        )
    )
    assert "category" not in text
    assert "account:" not in text
    assert "iban" not in text
    assert "status: open" in text
    assert 'booked: ""' in text
    assert 'paid_date: ""' in text


def test_no_line_items_section_when_empty() -> None:
    text = render(sample_invoice(line_items=[]))
    assert "## Line items" not in text
    assert text.endswith("# RE-2026-0912 — Muller GmbH\n")


def test_line_item_without_quantity_and_amount() -> None:
    text = render(sample_invoice(line_items=[{"description": "Position 1"}]))
    assert "- Position 1\n" in text


def test_line_item_fractional_quantity() -> None:
    text = render(
        sample_invoice(line_items=[{"description": "Meter", "quantity": "1.5", "amount": "9.90"}])
    )
    assert "- 1.5x Meter ... 9.90" in text


def test_scalar_quoting() -> None:
    text = render(
        sample_invoice(
            id="-2026/0912",
            vendor="Colon: Maker",
            account="true",
            reference="1234",
        )
    )
    assert 'id: "-2026/0912"' in text
    assert 'vendor: "Colon: Maker"' in text
    assert 'account: "true"' in text
    assert 'reference: "1234"' in text


def test_write_markdown_uses_lf_newlines(tmp_path: Path) -> None:
    target = tmp_path / "out.md"
    write_markdown(target, "# line1\n# line2\n")
    raw = target.read_bytes()
    assert b"\r\n" not in raw
    assert raw.endswith(b"\n")


def test_output_filename_sanitized() -> None:
    assert output_filename("RE-2026-0912") == "RE-2026-0912"
    assert output_filename('a<b>c:d"e/f\\g|h?i*j') == "a_b_c_d_e_f_g_h_i_j"
    assert output_filename("trailing. ") == "trailing"
    assert output_filename("") == "invoice"
    assert output_filename("CON") == "_CON"
    assert output_filename("x" * 500) == "x" * 150


def test_unique_output_path_collision_suffix(tmp_path: Path) -> None:
    (tmp_path / "RE-2026-0912.md").write_text("x", encoding="utf-8")
    first = unique_output_path(tmp_path, "RE-2026-0912")
    assert first.name == "RE-2026-0912-1.md"
    first.write_text("y", encoding="utf-8")
    second = unique_output_path(tmp_path, "RE-2026-0912")
    assert second.name == "RE-2026-0912-2.md"


def test_unique_output_path_no_collision(tmp_path: Path) -> None:
    assert unique_output_path(tmp_path, "RE-1") == tmp_path / "RE-1.md"


@pytest.mark.parametrize(
    ("amount", "expected"),
    [
        ("0", "amount: 0.00"),
        ("12.3", "amount: 12.30"),
        ("119.00", "amount: 119.00"),
    ],
)
def test_amount_rendering_edges(amount: str, expected: str) -> None:
    text = render(sample_invoice(amount=Decimal(amount)))
    assert expected in text
