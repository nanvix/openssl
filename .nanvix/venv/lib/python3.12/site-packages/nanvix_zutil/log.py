# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Structured logging for nanvix_zutil.

Provides colored terminal output and ``--json`` mode. In JSON mode every
message is emitted as a single-line JSON object with ``level``, ``code``,
``message``, and an optional ``hint`` field.
"""

from __future__ import annotations

import json
import sys
from typing import NoReturn

# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------

_json_mode: bool = False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def set_json_mode(enabled: bool) -> None:
    """Enable or disable JSON output mode.

    When enabled every log call emits a single-line JSON object instead of
    colored plain text.

    Args:
        enabled: ``True`` to enable JSON mode, ``False`` to disable it.
    """
    global _json_mode
    _json_mode = enabled


def info(msg: str) -> None:
    """Emit an informational message.

    Args:
        msg: The message text.
    """
    if _json_mode:
        print(json.dumps({"level": "info", "message": msg}), flush=True)
    else:
        print(f"\033[36minfo:\033[0m {msg}", flush=True)


def success(msg: str) -> None:
    """Emit a success message.

    Args:
        msg: The message text.
    """
    if _json_mode:
        print(json.dumps({"level": "success", "message": msg}), flush=True)
    else:
        print(f"\033[32msuccess:\033[0m {msg}", flush=True)


def warning(msg: str) -> None:
    """Emit a warning message.

    Args:
        msg: The message text.
    """
    if _json_mode:
        print(
            json.dumps({"level": "warning", "message": msg}),
            file=sys.stderr,
            flush=True,
        )
    else:
        print(f"\033[33mwarning:\033[0m {msg}", file=sys.stderr, flush=True)


def error(msg: str, hint: str | None = None) -> None:
    """Emit an error message without exiting.

    Args:
        msg: The error message text.
        hint: Optional hint to help the user recover.
    """
    if _json_mode:
        obj: dict[str, object] = {"level": "error", "message": msg}
        if hint is not None:
            obj["hint"] = hint
        print(json.dumps(obj), file=sys.stderr, flush=True)
    else:
        print(f"\033[31merror:\033[0m {msg}", file=sys.stderr, flush=True)
        if hint is not None:
            print(f"\033[90mhint:\033[0m {hint}", file=sys.stderr, flush=True)


def fatal(msg: str, code: int = 1, hint: str | None = None) -> NoReturn:
    """Emit an error message and exit with the given exit code.

    Args:
        msg: The error message text.
        code: The process exit code (default ``1``).
        hint: Optional hint to help the user recover.
    """
    if _json_mode:
        obj: dict[str, object] = {"level": "error", "code": code, "message": msg}
        if hint is not None:
            obj["hint"] = hint
        print(json.dumps(obj), file=sys.stderr, flush=True)
    else:
        print(f"\033[31merror:\033[0m {msg}", file=sys.stderr, flush=True)
        if hint is not None:
            print(f"\033[90mhint:\033[0m {hint}", file=sys.stderr, flush=True)
    sys.exit(code)
