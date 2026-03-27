# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Build-time dependency management for nanvix_zutil consumers.

:class:`Buildroot` manages a directory that collects headers and static
libraries required to compile a consumer repository.  :class:`Dependency`
describes a single library fetched from a GitHub release.
"""

from __future__ import annotations

import tarfile
from dataclasses import dataclass
from pathlib import Path

from nanvix_zutil import github, log
from nanvix_zutil.exitcodes import EXIT_MISSING_DEP

# ---------------------------------------------------------------------------
# Default locations
# ---------------------------------------------------------------------------

_DEFAULT_BUILDROOT_DIR = Path(".nanvix") / "buildroot"


# ---------------------------------------------------------------------------
# Dependency descriptor
# ---------------------------------------------------------------------------


@dataclass
class Dependency:
    """A library dependency fetched from a GitHub release.

    Attributes:
        name: Short library name (e.g. ``"zlib"``).
        repo: GitHub repository in ``owner/name`` format
            (e.g. ``"nanvix/zlib"``).
        tag: Release tag to fetch (e.g. ``"v1.2.3"``).
        artifact_pattern: ``str.format``-style template for the asset file
            name.  Interpolated keys: ``{name}``, ``{machine}``,
            ``{mode}``, ``{mem}``.
        install_libs: List of ``.a`` file names to copy into
            ``<buildroot>/lib/``.  ``None`` copies all ``.a`` files found.
        install_headers: List of header file names to copy into
            ``<buildroot>/include/``.  ``None`` copies all ``.h`` files found.
    """

    name: str
    repo: str
    tag: str
    artifact_pattern: str = "{name}-{machine}-{mode}-{mem}.tar.bz2"
    install_libs: list[str] | None = None
    install_headers: list[str] | None = None


# ---------------------------------------------------------------------------
# Buildroot
# ---------------------------------------------------------------------------


class Buildroot:
    """Manages the build-time dependency root (headers and static libraries).

    Attributes:
        path: Absolute path to the buildroot directory.
    """

    def __init__(self, path: Path) -> None:
        """Initialise the Buildroot with an existing directory.

        Args:
            path: Path to the buildroot directory.
        """
        self.path = path

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @staticmethod
    def create(dest: Path | None = None) -> "Buildroot":
        """Create (or locate) the buildroot directory and return a
        :class:`Buildroot` instance.

        Args:
            dest: Directory to use.  Defaults to
                ``.nanvix/buildroot`` relative to the current working
                directory.

        Returns:
            A :class:`Buildroot` pointing at *dest*.
        """
        path = dest if dest is not None else _DEFAULT_BUILDROOT_DIR
        (path / "lib").mkdir(parents=True, exist_ok=True)
        (path / "include").mkdir(parents=True, exist_ok=True)
        log.info(f"Buildroot at {path}")
        return Buildroot(path.resolve())

    # ------------------------------------------------------------------
    # Dependency installation
    # ------------------------------------------------------------------

    def install_dep(
        self,
        dep: Dependency,
        machine: str = "hyperlight",
        deployment_mode: str = "multi-process",
        memory_size: str = "128mb",
        gh_token: str | None = None,
    ) -> None:
        """Download a dependency release and install its libraries and headers.

        The release asset is downloaded into ``.nanvix/cache/`` and then
        extracted.  Selected ``.a`` and ``.h`` files are copied into
        ``<buildroot>/lib/`` and ``<buildroot>/include/`` respectively.

        Args:
            dep: The :class:`Dependency` descriptor.
            machine: Target machine identifier.
            deployment_mode: Deployment mode string.
            memory_size: Memory size string.
            gh_token: Optional GitHub token.
        """
        asset_name = dep.artifact_pattern.format(
            name=dep.name,
            machine=machine,
            mode=deployment_mode,
            mem=memory_size,
        )

        cache_dir = self.path.parent / "cache"
        asset_path = github.download_release_asset(
            repo=dep.repo,
            tag=dep.tag,
            asset_name=asset_name,
            dest=cache_dir,
            gh_token=gh_token,
        )

        log.info(f"Extracting {asset_name}...")
        with tarfile.open(asset_path, "r:bz2") as tf:
            for member in tf.getmembers():
                member_path = Path(member.name)
                if member.isfile():
                    if member_path.suffix == ".a":
                        if dep.install_libs is None or member_path.name in (
                            dep.install_libs
                        ):
                            member.name = member_path.name
                            tf.extract(member, path=self.path / "lib", filter="data")
                    elif member_path.suffix == ".h":
                        if dep.install_headers is None or member_path.name in (
                            dep.install_headers
                        ):
                            member.name = member_path.name
                            tf.extract(
                                member, path=self.path / "include", filter="data"
                            )

        log.success(f"Installed {dep.name} into buildroot")

    # ------------------------------------------------------------------
    # Verification
    # ------------------------------------------------------------------

    def verify(self, required_libs: list[str]) -> None:
        """Assert that all required build-time library files are present.

        Args:
            required_libs: List of ``.a`` file names that must exist under
                ``<buildroot>/lib/``.

        Raises:
            SystemExit: With exit code ``3`` if any required file is missing.
        """
        for lib in required_libs:
            lib_path = self.path / "lib" / lib
            if not lib_path.exists():
                log.fatal(
                    f"Required library '{lib}' not found in buildroot at {self.path}",
                    code=EXIT_MISSING_DEP,
                    hint="Run `./z setup` to download build-time dependencies.",
                )
        log.success("Buildroot verification passed")
