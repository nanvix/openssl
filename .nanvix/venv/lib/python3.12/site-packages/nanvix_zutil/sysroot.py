# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Nanvix runtime sysroot management.

:class:`Sysroot` downloads and verifies the Nanvix runtime artifact from
GitHub releases.  The artifact is a ``.tar.bz2`` archive identified by
machine, deployment mode, and memory-size configuration.
"""

from __future__ import annotations

import tarfile
from pathlib import Path

from nanvix_zutil import github, log
from nanvix_zutil.exitcodes import EXIT_MISSING_DEP

# ---------------------------------------------------------------------------
# Default location
# ---------------------------------------------------------------------------

_DEFAULT_SYSROOT_DIR = Path(".nanvix") / "sysroot"

# ---------------------------------------------------------------------------
# Sysroot repository / tag constants
# ---------------------------------------------------------------------------

_SYSROOT_REPO = "nanvix/nanvix"
_SYSROOT_ASSET_PREFIX = "nanvix-{machine}-{mode}-release-{mem}"


# ---------------------------------------------------------------------------
# Public class
# ---------------------------------------------------------------------------


class Sysroot:
    """Manages the Nanvix runtime sysroot directory.

    Attributes:
        path: Absolute path to the sysroot directory.
    """

    def __init__(self, path: Path) -> None:
        """Initialise the Sysroot with an existing directory.

        Args:
            path: Path to the sysroot directory.
        """
        self.path = path

    # ------------------------------------------------------------------
    # Factory / download
    # ------------------------------------------------------------------

    @staticmethod
    def download(
        machine: str,
        deployment_mode: str,
        memory_size: str,
        tag: str,
        gh_token: str | None = None,
        dest: Path | None = None,
    ) -> "Sysroot":
        """Download and extract the Nanvix runtime artifact from GitHub releases.

        If the sysroot directory already exists the download is skipped.

        Args:
            machine: Target machine identifier (e.g. ``"hyperlight"``).
            deployment_mode: Deployment mode string (e.g.
                ``"multi-process"``).
            memory_size: Memory size string (e.g. ``"128mb"``).
            tag: GitHub release tag to fetch (e.g. ``"v1.2.3"``).
            gh_token: Optional GitHub personal access token.
            dest: Directory where the sysroot will be extracted.  Defaults
                to ``.nanvix/sysroot`` relative to the current working
                directory.

        Returns:
            A :class:`Sysroot` pointing at the extracted directory.
        """
        sysroot_dir = dest if dest is not None else _DEFAULT_SYSROOT_DIR

        if sysroot_dir.exists():
            if sysroot_dir.is_dir():
                log.info(f"Sysroot already present at {sysroot_dir}")
                return Sysroot(sysroot_dir.resolve())
            log.fatal(
                f"Sysroot path '{sysroot_dir}' exists but is not a directory.",
                code=EXIT_MISSING_DEP,
                hint="Remove or rename this path and re-run `./z setup` to download the Nanvix sysroot.",
            )

        asset_prefix = _SYSROOT_ASSET_PREFIX.format(
            machine=machine,
            mode=deployment_mode,
            mem=memory_size,
        )

        cache_dir = sysroot_dir.parent / "cache"
        asset_path = github.download_release_asset(
            repo=_SYSROOT_REPO,
            tag=tag,
            asset_name=asset_prefix,
            dest=cache_dir,
            gh_token=gh_token,
            match_prefix=True,
        )

        log.info(f"Extracting sysroot from {asset_path.name}…")
        sysroot_dir.mkdir(parents=True, exist_ok=True)
        with tarfile.open(asset_path, "r:bz2") as tf:
            tf.extractall(path=sysroot_dir, filter="data")

        log.success(f"Sysroot extracted to {sysroot_dir}")
        return Sysroot(sysroot_dir.resolve())

    # ------------------------------------------------------------------
    # Verification
    # ------------------------------------------------------------------

    def verify(self, required_files: list[str]) -> None:
        """Assert that all required runtime files are present in the sysroot.

        Args:
            required_files: List of relative file paths that must exist under
                the sysroot directory.

        Raises:
            SystemExit: With exit code ``3`` if any required file is missing.
        """
        for rel_path in required_files:
            full_path = self.path / rel_path
            if not full_path.exists():
                log.fatal(
                    f"Required sysroot file '{rel_path}' not found at {self.path}",
                    code=EXIT_MISSING_DEP,
                    hint="Run `./z setup` to download the Nanvix sysroot.",
                )
        log.success("Sysroot verification passed")
