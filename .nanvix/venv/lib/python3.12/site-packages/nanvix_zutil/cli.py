# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Argument parsing and subcommand dispatch for nanvix_zutil consumers.

This module is an internal implementation detail of :class:`~nanvix_zutil.ZScript`.
Consumer scripts do not construct the parser directly; they call
``ZScript.main()`` which delegates here.
"""

from __future__ import annotations

import argparse
import importlib.metadata

# ---------------------------------------------------------------------------
# Version helper
# ---------------------------------------------------------------------------


def _get_version() -> str:
    """Return the installed ``nanvix-zutil`` version string.

    Returns:
        Version string (e.g. ``"0.1.0"``), or ``"unknown"`` if the package
        metadata is unavailable.
    """
    try:
        return importlib.metadata.version("nanvix-zutil")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


# ---------------------------------------------------------------------------
# Parser factory
# ---------------------------------------------------------------------------

#: All lifecycle subcommand names.
SUBCOMMANDS: tuple[str, ...] = (
    "setup",
    "build",
    "test",
    "benchmark",
    "release",
    "clean",
    "help",
)

#: Human-readable descriptions for each subcommand.
_SUBCOMMAND_HELP: dict[str, str] = {
    "setup": "Prepare the build environment",
    "build": "Build the project",
    "test": "Run tests",
    "benchmark": "Run benchmarks",
    "release": "Package a release",
    "clean": "Remove build artifacts",
    "help": "Show help message",
}


def build_parser(prog: str = "./z") -> argparse.ArgumentParser:
    """Build and return the top-level argument parser.

    Args:
        prog: Program name shown in ``--help`` output.

    Returns:
        Fully configured :class:`argparse.ArgumentParser`.
    """
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Nanvix build script.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Emit machine-parseable JSON output",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s (nanvix-zutil {_get_version()})",
    )

    subparsers = parser.add_subparsers(dest="subcommand")

    for name in SUBCOMMANDS:
        subparsers.add_parser(name, help=_SUBCOMMAND_HELP[name])

    return parser
