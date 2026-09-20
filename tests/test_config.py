"""Tests for config resolution: flag > env > TOML > default."""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import isolate_config_env  # noqa: F401 — fixture import
from nexfin_invoice.config import DEFAULT_MODEL, load_config
from nexfin_invoice.errors import ConfigError


def test_defaults(isolate_config_env) -> None:  # noqa: F811
    config = load_config()
    assert config.api_key is None
    assert config.model == DEFAULT_MODEL
    assert config.categories == ()
    assert config.accounts == ()
    assert config.output_dir is None


def test_toml_file_via_env_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        "\n".join(
            [
                'api_key = "toml-key"',
                'model = "vendor/model-from-toml"',
                'categories = ["office", "travel"]',
                'accounts = ["Checking"]',
                'output_dir = "~/Rechnungen"',
                "unknown_future_key = true",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("NEXFIN_INVOICE_CONFIG", str(config_file))
    config = load_config()
    assert config.api_key == "toml-key"
    assert config.model == "vendor/model-from-toml"
    assert config.categories == ("office", "travel")
    assert config.accounts == ("Checking",)
    assert config.output_dir == Path("~/Rechnungen").expanduser()


def test_env_overrides_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_file = tmp_path / "config.toml"
    config_file.write_text('api_key = "toml-key"\nmodel = "toml-model"\n', encoding="utf-8")
    monkeypatch.setenv("NEXFIN_INVOICE_CONFIG", str(config_file))
    monkeypatch.setenv("OPENROUTER_API_KEY", "env-key")
    monkeypatch.setenv("NEXFIN_INVOICE_MODEL", "env-model")
    config = load_config()
    assert config.api_key == "env-key"
    assert config.model == "env-model"


def test_flag_overrides_env_and_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_file = tmp_path / "config.toml"
    config_file.write_text('model = "toml-model"\n', encoding="utf-8")
    monkeypatch.setenv("NEXFIN_INVOICE_CONFIG", str(config_file))
    monkeypatch.setenv("NEXFIN_INVOICE_MODEL", "env-model")
    config = load_config(model_flag="flag-model")
    assert config.model == "flag-model"


def test_flag_config_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_config(config_flag=tmp_path / "nope.toml")


def test_flag_config_path_used(tmp_path: Path) -> None:
    config_file = tmp_path / "custom.toml"
    config_file.write_text('api_key = "custom"\n', encoding="utf-8")
    config = load_config(config_flag=config_file)
    assert config.api_key == "custom"


def test_missing_env_config_path_ignored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEXFIN_INVOICE_CONFIG", str(tmp_path / "nope.toml"))
    config = load_config()
    assert config.model == DEFAULT_MODEL


def test_invalid_toml_raises(tmp_path: Path) -> None:
    config_file = tmp_path / "broken.toml"
    config_file.write_text("api_key = [not valid", encoding="utf-8")
    with pytest.raises(ConfigError, match="TOML"):
        load_config(config_flag=config_file)


def test_wrong_types_raise(tmp_path: Path) -> None:
    config_file = tmp_path / "wrong.toml"
    config_file.write_text("categories = \"office\"\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="categories"):
        load_config(config_flag=config_file)


def test_empty_model_string_raises(tmp_path: Path) -> None:
    config_file = tmp_path / "empty.toml"
    config_file.write_text('model = ""\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="model"):
        load_config(config_flag=config_file)


def test_default_config_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_file = tmp_path / "config.toml"
    config_file.write_text('model = "default-path-model"\n', encoding="utf-8")
    monkeypatch.setattr("nexfin_invoice.config.DEFAULT_CONFIG_PATH", config_file)
    assert load_config().model == "default-path-model"


def test_default_config_path_missing_is_fine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("nexfin_invoice.config.DEFAULT_CONFIG_PATH", tmp_path / "nope.toml")
    assert load_config().api_key is None
