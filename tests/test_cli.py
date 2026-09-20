"""End-to-end CLI tests on synthetic PDFs (no network, no real API key)."""

from __future__ import annotations

from pathlib import Path

import httpx

from conftest import (
    CII_INVOICE_XML,
    INVOICE_TEXT_LINES,
    completion,
    invoice_json,
    make_zugferd_pdf,
    patch_ai_client,
)
from nexfin_invoice import cli


def _ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json=completion(invoice_json()))


def test_convert_writes_file(tmp_path: Path, capsys, isolate_config_env) -> None:
    pdf = make_zugferd_pdf(tmp_path / "invoice.pdf", CII_INVOICE_XML)
    out = tmp_path / "out"
    rc = cli.main(["convert", str(pdf), "-o", str(out)])
    assert rc == 0
    written = out / "RE-2026-0912.md"
    assert written.is_file()
    content = written.read_text(encoding="utf-8")
    assert content.startswith("---\nid: RE-2026-0912")
    assert "amount: -119.00" in content
    assert "[zugferd]" in capsys.readouterr().out


def test_convert_creates_output_dir(tmp_path: Path, isolate_config_env) -> None:
    pdf = make_zugferd_pdf(tmp_path / "invoice.pdf", CII_INVOICE_XML)
    out = tmp_path / "deep" / "nested"
    assert cli.main(["convert", str(pdf), "-o", str(out)]) == 0
    assert (out / "RE-2026-0912.md").is_file()


def test_collision_suffix_convention(tmp_path: Path, isolate_config_env) -> None:
    pdf = make_zugferd_pdf(tmp_path / "invoice.pdf", CII_INVOICE_XML)
    out = tmp_path / "out"
    assert cli.main(["convert", str(pdf), str(pdf), "-o", str(out)]) == 0
    names = sorted(p.name for p in out.iterdir())
    assert names == ["RE-2026-0912-1.md", "RE-2026-0912.md"]


def test_stdout_mode(tmp_path: Path, capsys, isolate_config_env) -> None:
    pdf = make_zugferd_pdf(tmp_path / "invoice.pdf", CII_INVOICE_XML)
    rc = cli.main(["convert", str(pdf), "--stdout"])
    assert rc == 0
    out = capsys.readouterr().out
    assert out.startswith("---\nid: RE-2026-0912")
    assert out.rstrip("\n").endswith("# RE-2026-0912 — Muller GmbH")


def test_ai_path_via_cli(tmp_path: Path, capsys, monkeypatch, isolate_config_env) -> None:
    pdf = make_zugferd_pdf(
        tmp_path / "invoice.pdf", CII_INVOICE_XML, text_lines=INVOICE_TEXT_LINES
    )
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    patch_ai_client(monkeypatch, _ok)
    out = tmp_path / "out"
    rc = cli.main(["convert", str(pdf), "--ai", "-o", str(out)])
    assert rc == 0
    assert (out / "RE-2026-0777.md").is_file()
    assert "[ai-text]" in capsys.readouterr().out


def test_failure_exit_code_and_stderr(tmp_path: Path, capsys, isolate_config_env) -> None:
    garbage = tmp_path / "garbage.pdf"
    garbage.write_bytes(b"junk")
    rc = cli.main(["convert", str(garbage), "-o", str(tmp_path / "out")])
    assert rc == 1
    assert "garbage.pdf" in capsys.readouterr().err


def test_missing_output_location_is_usage_error(tmp_path: Path, capsys, isolate_config_env) -> None:
    pdf = make_zugferd_pdf(tmp_path / "invoice.pdf", CII_INVOICE_XML)
    rc = cli.main(["convert", str(pdf)])
    assert rc == 2
    assert "output" in capsys.readouterr().err


def test_missing_config_file_is_usage_error(tmp_path: Path, capsys, isolate_config_env) -> None:
    pdf = make_zugferd_pdf(tmp_path / "invoice.pdf", CII_INVOICE_XML)
    rc = cli.main(["convert", str(pdf), "--stdout", "--config", str(tmp_path / "nope.toml")])
    assert rc == 2
    assert "config" in capsys.readouterr().err


def test_output_dir_from_config(tmp_path: Path, isolate_config_env) -> None:
    out = tmp_path / "from-config"
    config_file = tmp_path / "config.toml"
    config_file.write_text(f"output_dir = '{out}'\n", encoding="utf-8")
    pdf = make_zugferd_pdf(tmp_path / "invoice.pdf", CII_INVOICE_XML)
    rc = cli.main(
        [
            "convert",
            str(pdf),
            "--config",
            str(config_file),
        ]
    )
    assert rc == 0
    assert (out / "RE-2026-0912.md").is_file()


def test_output_dir_flag_beats_config(tmp_path: Path, isolate_config_env) -> None:
    config_file = tmp_path / "config.toml"
    config_file.write_text('output_dir = "from-config"\n', encoding="utf-8")
    pdf = make_zugferd_pdf(tmp_path / "invoice.pdf", CII_INVOICE_XML)
    rc = cli.main(
        ["convert", str(pdf), "--config", str(config_file), "-o", str(tmp_path / "from-flag")]
    )
    assert rc == 0
    assert (tmp_path / "from-flag" / "RE-2026-0912.md").is_file()
    assert not (tmp_path / "from-config").exists()


def test_version(capsys) -> None:
    import pytest

    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--version"])
    assert excinfo.value.code == 0
    assert "nexfin-invoice" in capsys.readouterr().out
