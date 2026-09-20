"""Command-line interface: ``nexfin-invoice convert FILES...``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .config import load_config
from .errors import ConfigError, ConversionError
from .pipeline import convert
from .render import unique_output_path, write_markdown

__all__ = ["main"]


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "convert":
        return _cmd_convert(args)
    parser.error("a subcommand is required")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nexfin-invoice",
        description="Convert invoice PDFs into nexfin inbox Markdown files.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command")
    convert_parser = subparsers.add_parser(
        "convert", help="convert invoice PDFs to .md files"
    )
    convert_parser.add_argument("files", nargs="+", type=Path, help="invoice PDF files")
    convert_parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=None,
        help="directory for the generated .md files (default: output_dir from config)",
    )
    convert_parser.add_argument(
        "--stdout",
        action="store_true",
        help="print the markdown to stdout instead of writing files",
    )
    convert_parser.add_argument(
        "--model",
        default=None,
        help="OpenRouter model for AI extraction (default: config or %(default)s)",
    )
    convert_parser.add_argument(
        "--ai",
        action="store_true",
        help="skip embedded ZUGFeRD XML parsing and force AI extraction",
    )
    convert_parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="path to config.toml (default: ~/.config/nexfin-invoice/config.toml)",
    )
    return parser


def _cmd_convert(args: argparse.Namespace) -> int:
    try:
        config = load_config(
            model_flag=args.model,
            config_flag=args.config,
        )
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    output_dir: Path | None = None
    if not args.stdout:
        output_dir = args.output_dir or config.output_dir
        if output_dir is None:
            print(
                "error: no output location. Use -o DIR, --stdout, "
                "or set output_dir in the config file.",
                file=sys.stderr,
            )
            return 2

    failures = 0
    for source in args.files:
        try:
            result = convert(source, config, force_ai=args.ai)
        except ConversionError as exc:
            print(f"{source}: {exc}", file=sys.stderr)
            failures += 1
            continue
        if args.stdout:
            sys.stdout.write(result.markdown)
        else:
            assert output_dir is not None
            output_dir.mkdir(parents=True, exist_ok=True)
            destination = unique_output_path(output_dir, result.invoice.id)
            write_markdown(destination, result.markdown)
            print(f"{source} -> {destination} [{result.method}]")
    return 1 if failures else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
