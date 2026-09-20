"""Configuration: CLI flag > environment variable > TOML config > default.

Config file location: ``~/.config/nexfin-invoice/config.toml``, overridable
via ``NEXFIN_INVOICE_CONFIG`` or the ``--config`` flag. Recognized keys:
``api_key``, ``model``, ``categories = [...]``, ``accounts = [...]``,
``output_dir``. Unknown keys are ignored (forward compatibility).
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import ConfigError

__all__ = ["DEFAULT_CONFIG_PATH", "DEFAULT_MODEL", "Config", "load_config"]

DEFAULT_MODEL = "google/gemini-2.5-flash"
DEFAULT_CONFIG_PATH = Path("~/.config/nexfin-invoice/config.toml")


@dataclass(frozen=True)
class Config:
    """Resolved runtime configuration."""

    api_key: str | None = None
    model: str = DEFAULT_MODEL
    categories: tuple[str, ...] = ()
    accounts: tuple[str, ...] = ()
    output_dir: Path | None = None


def load_config(
    *,
    model_flag: str | None = None,
    config_flag: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> Config:
    """Resolve configuration from flag, environment and TOML file."""
    env = os.environ if environ is None else environ
    path = _resolve_config_path(config_flag, env)
    toml_data = _read_toml(path) if path is not None else {}

    env_model = env.get("NEXFIN_INVOICE_MODEL")
    return Config(
        api_key=_api_key(env, toml_data),
        model=model_flag or env_model or _toml_str(toml_data, "model") or DEFAULT_MODEL,
        categories=_toml_str_list(toml_data, "categories"),
        accounts=_toml_str_list(toml_data, "accounts"),
        output_dir=_output_dir(toml_data),
    )


def _resolve_config_path(
    config_flag: str | Path | None, env: Mapping[str, str]
) -> Path | None:
    if config_flag is not None:
        path = Path(config_flag).expanduser()
        if not path.is_file():
            raise ConfigError(f"config file not found: {path}")
        return path
    env_path = env.get("NEXFIN_INVOICE_CONFIG", "")
    if env_path:
        path = Path(env_path).expanduser()
        if path.is_file():
            return path
        return None
    default = DEFAULT_CONFIG_PATH.expanduser()
    return default if default.is_file() else None


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with open(path, "rb") as handle:
            data = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {path}: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"cannot read config file {path}: {exc}") from exc
    if not isinstance(data, dict):  # pragma: no cover — tomllib always returns a dict
        raise ConfigError(f"invalid config file {path}")
    return data


def _api_key(env: Mapping[str, str], toml_data: dict[str, Any]) -> str | None:
    key = env.get("OPENROUTER_API_KEY") or _toml_str(toml_data, "api_key")
    return key or None


def _toml_str(toml_data: dict[str, Any], key: str) -> str | None:
    value = toml_data.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"config key {key!r} must be a non-empty string")
    return value.strip()


def _toml_str_list(toml_data: dict[str, Any], key: str) -> tuple[str, ...]:
    value = toml_data.get(key)
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigError(f"config key {key!r} must be a list of strings")
    return tuple(item for item in value if item.strip())


def _output_dir(toml_data: dict[str, Any]) -> Path | None:
    value = toml_data.get("output_dir")
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ConfigError("config key 'output_dir' must be a non-empty string")
    return Path(value).expanduser()
