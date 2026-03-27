# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Persistent key-value configuration for nanvix_zutil consumers.

Configuration is stored at ``.nanvix/env.json`` and overridden by environment
variables at runtime.  The precedence order (highest to lowest) is:

1. Environment variables
2. Persisted ``.nanvix/env.json``
3. Built-in defaults
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import cast

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

_DEFAULTS: dict[str, str] = {
    "NANVIX_MACHINE": "hyperlight",
    "NANVIX_DEPLOYMENT_MODE": "multi-process",
    "NANVIX_MEMORY_SIZE": "128mb",
}

# ---------------------------------------------------------------------------
# Standard config key names
# ---------------------------------------------------------------------------

CFG_SYSROOT: str = "NANVIX_SYSROOT"
"""Path to the downloaded Nanvix sysroot directory."""

CFG_TOOLCHAIN: str = "NANVIX_TOOLCHAIN"
"""Path to the Nanvix cross-compilation toolchain."""

CFG_TAG: str = "NANVIX_TAG"
"""Nanvix sysroot release tag to download."""

CFG_GH_TOKEN: str = "GH_TOKEN"
"""GitHub token for authenticated API requests (rate limits)."""


# ---------------------------------------------------------------------------
# Public class
# ---------------------------------------------------------------------------


class Config:
    """Persistent key-value configuration stored at ``.nanvix/env.json``.

    Initialisation order:

    1. Seed with built-in defaults.
    2. Override with values from the persisted ``.nanvix/env.json`` file,
       if it exists.
    3. Override with environment variables (including extra ``NANVIX_*`` keys
       and ``GH_TOKEN``), which always take precedence.

    Effective precedence (highest to lowest) is therefore:

    1. Environment variables
    2. Persisted ``.nanvix/env.json``
    3. Built-in defaults

    Attributes:
        machine: Target machine identifier (e.g. ``"hyperlight"``).
        deployment_mode: Deployment mode (``"single-process"``,
            ``"multi-process"``, or ``"standalone"``).
        memory_size: Memory size string used in artifact names
            (e.g. ``"128mb"``).
    """

    def __init__(self, nanvix_dir: Path) -> None:
        """Initialise configuration from environment and persisted state.

        Args:
            nanvix_dir: Path to the ``.nanvix/`` directory of the consumer
                repository.
        """
        self._nanvix_dir = nanvix_dir
        self._config_path = nanvix_dir / "env.json"
        self._data: dict[str, str] = {}

        # Seed with defaults.
        self._data.update(_DEFAULTS)

        # Load persisted values (environment still wins below).
        if self._config_path.exists():
            self.load()
            # Never persist secrets such as GH_TOKEN; strip if present.
            self._data.pop("GH_TOKEN", None)

        # Apply environment variable overrides.
        for key in list(self._data.keys()):
            env_val = os.environ.get(key)
            if env_val is not None:
                self._data[key] = env_val

        # Apply any extra env vars not in defaults.
        for key, val in os.environ.items():
            if key.startswith("NANVIX_"):
                self._data[key] = val

    # ------------------------------------------------------------------
    # Convenience properties
    # ------------------------------------------------------------------

    @property
    def machine(self) -> str:
        """Target machine identifier."""
        return self._data.get("NANVIX_MACHINE", _DEFAULTS["NANVIX_MACHINE"])

    @property
    def deployment_mode(self) -> str:
        """Deployment mode string."""
        return self._data.get(
            "NANVIX_DEPLOYMENT_MODE", _DEFAULTS["NANVIX_DEPLOYMENT_MODE"]
        )

    @property
    def memory_size(self) -> str:
        """Memory size string."""
        return self._data.get("NANVIX_MEMORY_SIZE", _DEFAULTS["NANVIX_MEMORY_SIZE"])

    # ------------------------------------------------------------------
    # Generic get / set
    # ------------------------------------------------------------------

    def get(self, key: str, default: str | None = None) -> str | None:
        """Retrieve a configuration value.

        Environment variables always take precedence.

        Args:
            key: The configuration key.
            default: Value returned when the key is absent.

        Returns:
            The configuration value or *default*.
        """
        env_val = os.environ.get(key)
        if env_val is not None:
            return env_val
        return self._data.get(key, default)

    def set(self, key: str, value: str) -> None:
        """Set a configuration value in memory.

        Call :meth:`save` to persist the change to disk.

        Args:
            key: The configuration key.
            value: The new value.
        """
        self._data[key] = value

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self) -> None:
        """Persist the current in-memory configuration to ``.nanvix/env.json``."""
        self._nanvix_dir.mkdir(parents=True, exist_ok=True)
        with self._config_path.open("w", encoding="utf-8") as fh:
            json.dump(self._data, fh, indent=2)

    def load(self) -> None:
        """Reload configuration from ``.nanvix/env.json``.

        Missing or malformed files are silently ignored.
        """
        if not self._config_path.exists():
            return
        try:
            with self._config_path.open("r", encoding="utf-8") as fh:
                raw: object = json.load(fh)
            if not isinstance(raw, dict):
                return
            persisted = cast(dict[str, object], raw)
            for k, v in persisted.items():
                if isinstance(v, str):
                    # Environment variables still win.
                    if os.environ.get(k) is None:
                        self._data[k] = v
        except (json.JSONDecodeError, OSError):
            pass
