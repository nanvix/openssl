# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Nanvix build script for OpenSSL.

Usage:
    ./z setup     # Download Nanvix sysroot
    ./z build     # Cross-compile libcrypto.a, libssl.a, and test ELF
    ./z test      # Run test suite (smoke + integration + functional)
    ./z release   # Package release tarball
    ./z clean     # Remove build artifacts
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

from nanvix_zutil import (
    CFG_SYSROOT,
    EXIT_BUILD_FAILURE,
    EXIT_MISSING_DEP,
    TOOLCHAIN_CONTAINER_PATH,
    ZScript,
    log,
    make_initrd,
    run,
)

_EXIT_BUILD: int = EXIT_BUILD_FAILURE
_EXIT_DEP: int = EXIT_MISSING_DEP

# Makefile variable names (build-system-specific).
_MAKE_VAR_HOME = "NANVIX_HOME"
_MAKE_VAR_TOOLCHAIN = "NANVIX_TOOLCHAIN"
_MAKE_VAR_PLATFORM = "PLATFORM"
_MAKE_VAR_PROCESS_MODE = "PROCESS_MODE"
_MAKE_VAR_MEMORY_SIZE = "MEMORY_SIZE"
_MAKE_VAR_INSTALL_PREFIX = "INSTALL_PREFIX"

# OpenSSL embeds --prefix into compiled artifacts (OPENSSLDIR, etc.).
# Use /sysroot so that release tarballs don't contain ephemeral runner paths.
_DEFAULT_INSTALL_PREFIX = "/sysroot"

# Docker image for cross-compilation (must match Makefile.nanvix default).
_DOCKER_IMAGE = "ghcr.io/nanvix/toolchain-gcc:sha-34a3641"

# Test binary name produced by the Makefile.
_TEST_ELF = "openssl_nanvix_test.elf"

IS_WINDOWS = sys.platform == "win32"


class OpenSSLBuild(ZScript):
    """Build script for nanvix/openssl."""

    def docker_image(self) -> str:
        """Return the Docker image for cross-compilation."""
        return _DOCKER_IMAGE

    def _ensure_docker_perl(self) -> None:
        """Ensure the Docker image has Perl with FindBin (needed by Configure).

        The base toolchain image ships only ``perl-base``.  OpenSSL's
        ``./Configure`` requires the full ``perl`` package (``FindBin``).
        When Docker is active, this method builds a thin derived image
        that adds ``perl`` on top of the base image, then switches
        ``self.docker`` to use it.  The derived image is cached locally
        so subsequent calls are instant.
        """
        if not self.docker:  # type: ignore[reportUnknownMemberType]
            return

        base: str = self.docker.image  # type: ignore[reportUnknownMemberType]
        derived = f"{base}-openssl"

        # Fast path: derived image already built from a previous run.
        if (
            subprocess.run(
                ["docker", "image", "inspect", derived],
                capture_output=True,
            ).returncode
            == 0
        ):
            self.docker = self.docker_config(derived)
            return

        # Check whether the base image already ships FindBin.
        if (
            subprocess.run(
                ["docker", "run", "--rm", base, "perl", "-MFindBin", "-e1"],
                capture_output=True,
            ).returncode
            == 0
        ):
            return

        # Build a derived image that adds perl.
        log.info("Building derived Docker image with Perl (required by OpenSSL)...")
        subprocess.run(
            ["docker", "build", "-t", derived, "-"],
            input=(
                f"FROM {base}\n"
                "RUN apt-get update -qq && "
                "DEBIAN_FRONTEND=noninteractive apt-get install -y -qq perl && "
                "rm -rf /var/lib/apt/lists/*\n"
            ),
            text=True,
            check=True,
        )
        self.docker = self.docker_config(derived)

    def _make_args(
        self,
        *targets: str,
        with_install_prefix: bool = True,
    ) -> list[str]:
        """Build the common make argument list.

        Path translation for ``NANVIX_HOME`` is applied when running
        under Docker (``self.docker`` is set); otherwise the raw host
        path is used.  ``NANVIX_TOOLCHAIN`` is always the in-container
        toolchain path because the only goals that dereference it run
        under Docker.
        """
        sysroot = self.config.get(CFG_SYSROOT, "")
        if not sysroot:
            log.fatal(
                f"{CFG_SYSROOT} is not set.",
                code=_EXIT_DEP,
                hint="Run `./z setup` first to download the sysroot.",
            )
        toolchain_p = str(TOOLCHAIN_CONTAINER_PATH)
        sysroot_p = (
            self.docker.translate_path(Path(sysroot)) if self.docker else Path(sysroot)
        )

        args = [
            "make",
            "-f",
            "Makefile.nanvix",
            f"{_MAKE_VAR_HOME}={sysroot_p}",
            f"{_MAKE_VAR_TOOLCHAIN}={toolchain_p}",
        ]

        args.extend(
            [
                f"{_MAKE_VAR_PLATFORM}={self.config.machine}",
                f"{_MAKE_VAR_PROCESS_MODE}={self.config.deployment_mode}",
                f"{_MAKE_VAR_MEMORY_SIZE}={self.config.memory_size}",
            ]
        )

        if with_install_prefix:
            args.append(f"{_MAKE_VAR_INSTALL_PREFIX}={_DEFAULT_INSTALL_PREFIX}")

        args.extend(targets)
        return args

    # ------------------------------------------------------------------
    # Core lifecycle: setup / build / test / release / clean
    # ------------------------------------------------------------------

    def setup(self) -> bool:
        """Download the Nanvix sysroot."""
        return super().setup()

    def build(self) -> None:
        """Cross-compile libcrypto.a, libssl.a, and test ELF (in Docker)."""
        self._ensure_docker_perl()
        # Build libraries and test binary in one pass.
        run(
            *self._make_args("all", _TEST_ELF),
            cwd=self.repo_root,
            docker=self.docker,
        )

    def test(self) -> None:
        """Run tests natively on the host (no Docker).

        Smoke and integration tests are always delegated to the Makefile.
        The functional test in standalone mode is handled in Python via
        make_initrd so that initrd creation is shared across platforms.
        """
        if IS_WINDOWS:
            self._run_tests_windows()
            return

        self._require_build_artifacts()

        # Normalize short aliases to canonical Makefile target names.
        _alias_map: dict[str, str] = {
            "smoke": "test-smoke",
            "integration": "test-integration",
            "functional": "test-functional",
        }
        targets = [_alias_map.get(t, t) for t in (self.targets if self.targets else [])]

        if self.config.deployment_mode == "standalone":
            _functional_targets = {"test", "test-functional"}
            needs_functional = not targets or bool(set(targets) & _functional_targets)
            make_targets = [t for t in targets if t not in _functional_targets]
            if not targets:
                make_targets = ["test-smoke", "test-integration"]
            elif needs_functional and not make_targets:
                if "test" in targets:
                    make_targets = ["test-smoke", "test-integration"]
                else:
                    make_targets = ["test-integration"]
            if make_targets:
                run(*self._make_args(*make_targets), cwd=self.repo_root)
            if needs_functional:
                self._run_functional_standalone()
        else:
            run_targets = targets if targets else ["test"]
            run(*self._make_args(*run_targets), cwd=self.repo_root)

    def release(self) -> None:
        """Package the release tarball natively (no Docker).

        Uses Python's tarfile module for gzip compression.
        """
        repo = self.repo_root
        libcrypto = repo / "libcrypto.a"
        libssl = repo / "libssl.a"
        headers_dir = repo / "include" / "openssl"
        test_elf = repo / _TEST_ELF

        for path in (libcrypto, libssl, headers_dir):
            if not path.exists():
                log.fatal(
                    f"release: missing artefact {path}",
                    code=_EXIT_BUILD,
                    hint="Run `./z build` first.",
                )

        artifact = (
            f"openssl-{self.config.machine}"
            f"-{self.config.deployment_mode}"
            f"-{self.config.memory_size}"
        )
        dist_dir = repo / "dist"
        staging = dist_dir / artifact
        sysroot = staging / "sysroot"

        # Fresh stage every time.
        if staging.exists():
            shutil.rmtree(staging)
        (sysroot / "lib").mkdir(parents=True)
        (sysroot / "include" / "openssl").mkdir(parents=True)
        (sysroot / "bin").mkdir(parents=True)

        # Copy libraries.
        shutil.copy2(libcrypto, sysroot / "lib" / "libcrypto.a")
        shutil.copy2(libssl, sysroot / "lib" / "libssl.a")

        # Copy headers.
        for h in sorted(headers_dir.glob("*.h")):
            shutil.copy2(h, sysroot / "include" / "openssl" / h.name)

        # Copy test ELF if available.
        if test_elf.is_file():
            shutil.copy2(test_elf, sysroot / "bin" / test_elf.name)

        # Build gzip-compressed tarball using Python's tarfile module.
        tarball = dist_dir / f"{artifact}.tar.gz"
        if tarball.exists():
            tarball.unlink()
        with tarfile.open(tarball, "w:gz") as tf:
            tf.add(sysroot, arcname="sysroot")
        log.info(f"Wrote release tarball: {tarball}")

        self._verify_release(tarball)

    def clean(self) -> None:
        """Remove build artifacts."""
        run(
            "make",
            "-f",
            "Makefile.nanvix",
            "clean",
            cwd=self.repo_root,
        )

    # ------------------------------------------------------------------
    # Preflight
    # ------------------------------------------------------------------

    def _require_build_artifacts(self) -> None:
        """Fatal-out early when build outputs aren't present.

        The Makefile recipes will catch this too, but emitting it from
        Python keeps the failure mode consistent across platforms/modes
        (Linux/Windows, microvm/standalone) and points the user at the
        right remediation without needing to parse make output.
        """
        required = [
            self.repo_root / "libcrypto.a",
            self.repo_root / "libssl.a",
            self.repo_root / "include" / "openssl" / "opensslv.h",
            self.repo_root / _TEST_ELF,
        ]
        missing = [p for p in required if not p.exists()]
        if missing:
            log.fatal(
                "test: missing build artefact(s): "
                + ", ".join(str(p.relative_to(self.repo_root)) for p in missing),
                code=_EXIT_BUILD,
                hint="Run `./z build` first.",
            )

    # ------------------------------------------------------------------
    # Functional tests
    # ------------------------------------------------------------------

    def _run_functional_standalone(self) -> None:
        """Run standalone functional tests using make_initrd.

        Creates an initrd bundling the test ELF with system daemons via
        make_initrd, and a ramfs providing /tmp for test I/O.
        """
        elf = self.repo_root / _TEST_ELF
        if not elf.is_file():
            log.fatal(
                f"{_TEST_ELF} not found.",
                code=_EXIT_BUILD,
                hint="Run `./z build` first.",
            )

        sysroot = self._sysroot_path()
        mkramfs = sysroot / "bin" / "mkramfs.elf"
        nanvixd = sysroot / "bin" / "nanvixd.elf"
        for tool in (mkramfs, nanvixd):
            if not tool.is_file():
                log.fatal(
                    f"functional: {tool} not found",
                    code=_EXIT_DEP,
                    hint="Run `./z setup` first.",
                )

        log.info("=== openssl functional tests ===")
        log.info(f"  Running {_TEST_ELF} via nanvixd standalone...")

        initrd = make_initrd(self, _TEST_ELF)
        try:
            with tempfile.TemporaryDirectory(prefix="openssl_test_") as tmp:
                tmp_path = Path(tmp)
                ramfs_dir = tmp_path / "ramfs"
                ramfs_dir.mkdir()
                (ramfs_dir / "tmp").mkdir()
                ramfs_img = tmp_path / "rootfs.img"

                run(
                    str(mkramfs),
                    "-o",
                    str(ramfs_img),
                    str(ramfs_dir),
                    timeout=60,
                )

                run(
                    str(nanvixd),
                    "-bin-dir",
                    str(sysroot / "bin"),
                    "-ramfs",
                    str(ramfs_img),
                    "--",
                    str(initrd),
                    timeout=180,
                )
        finally:
            if initrd.exists():
                initrd.unlink()

        log.info(
            f"  PASS: openssl library test {self.config.deployment_mode} (exit code 0)"
        )
        log.info("  PASS: openssl functional tests")
        log.info("=== All openssl tests PASSED ===")

    def _sysroot_path(self) -> Path:
        """Return the host-side sysroot path."""
        sysroot = self.config.get(CFG_SYSROOT, "")
        if not sysroot:
            log.fatal(
                f"{CFG_SYSROOT} is not set.",
                code=_EXIT_DEP,
                hint="Run `./z setup` first.",
            )
        return Path(sysroot)

    # ------------------------------------------------------------------
    # Windows test support
    # ------------------------------------------------------------------

    def _run_tests_windows(self) -> None:
        """Run tests natively on Windows using nanvixd.exe.

        Uses make_initrd to bundle the binary with system daemons,
        and a ramfs for test I/O. Only standalone mode is supported
        on Windows.
        """
        if self.config.deployment_mode != "standalone":
            print(
                f"Skipping tests on Windows for mode"
                f" '{self.config.deployment_mode}'"
                " (single-process and multi-process require linuxd,"
                " Linux only)."
            )
            return
        sysroot = self.config.get(CFG_SYSROOT, "")
        if not sysroot:
            log.fatal(
                f"{CFG_SYSROOT} is not set.",
                code=_EXIT_DEP,
                hint="Run `./z setup` first.",
            )
        sysroot_path = Path(sysroot)
        nanvixd = sysroot_path / "bin" / "nanvixd.exe"
        mkramfs = sysroot_path / "bin" / "mkramfs.exe"
        if not nanvixd.is_file():
            log.fatal(
                "nanvixd.exe not found.",
                code=_EXIT_DEP,
                hint="Run `./z setup` first.",
            )
        if not mkramfs.is_file():
            log.fatal(
                "mkramfs.exe not found.",
                code=_EXIT_DEP,
                hint="Run `./z setup` first.",
            )

        build_dir = self.repo_root / "build"
        test_binaries = sorted(build_dir.glob("*.elf")) if build_dir.is_dir() else []

        if not test_binaries:
            print("No test binaries found in build/ -- smoke test only.")
            print("OK: library-only repo, no functional tests to run on Windows")
            return

        failed: list[str] = []
        for binary in test_binaries:
            name = binary.stem
            print(f"RUN  {name}...")
            # make_initrd resolves binaries relative to repo_root;
            # copy the ELF there temporarily unless it already lives there.
            repo_elf = self.repo_root / binary.name
            copied_elf = False
            initrd: Path | None = None
            try:
                if binary.resolve() != repo_elf.resolve():
                    if repo_elf.exists():
                        raise FileExistsError(
                            f"refusing to clobber existing {repo_elf}"
                        )
                    shutil.copy2(binary, repo_elf)
                    copied_elf = True
                initrd = make_initrd(self, binary.name)
                with tempfile.TemporaryDirectory(prefix=f"nanvix_{name}_") as tmpdir:
                    tmpdir_path = Path(tmpdir)
                    ramfs_dir = tmpdir_path / "ramfs"
                    ramfs_dir.mkdir()
                    (ramfs_dir / "tmp").mkdir(exist_ok=True)
                    ramfs_img = tmpdir_path / f"rootfs_{name}.img"

                    run(
                        str(mkramfs),
                        "-o",
                        str(ramfs_img),
                        str(ramfs_dir),
                        timeout=60,
                    )

                    run(
                        str(nanvixd),
                        "-bin-dir",
                        str(sysroot_path / "bin"),
                        "-ramfs",
                        str(ramfs_img),
                        "--",
                        str(initrd),
                        timeout=120,
                    )
                print(f"OK   {name}")
            except SystemExit:
                print(f"FAIL {name}")
                failed.append(name)
            finally:
                if initrd is not None and initrd.exists():
                    initrd.unlink()
                if copied_elf and repo_elf.exists():
                    repo_elf.unlink()

        if failed:
            msg = " ".join(failed)
            raise RuntimeError(f"{len(failed)} test(s) failed: {msg}")
        print(f"\t\t*** All {len(test_binaries)} tests PASSED ***")

    # ------------------------------------------------------------------
    # Release verification
    # ------------------------------------------------------------------

    def _verify_release(self, tarball: Path) -> None:
        """Verify the release tarball contains expected paths."""
        required = {
            "sysroot/lib/libcrypto.a",
            "sysroot/lib/libssl.a",
        }
        with tarfile.open(tarball, "r:gz") as tf:
            members = set(tf.getnames())
        missing = sorted(required - members)
        if missing:
            log.fatal(
                f"release: tarball missing required paths: {missing}",
                code=_EXIT_BUILD,
                hint=f"Tarball: {tarball}",
            )
        if not any(
            m.startswith("sysroot/include/openssl/") and m != "sysroot/include/openssl"
            for m in members
        ):
            log.fatal(
                "release: tarball has no entries under sysroot/include/openssl/",
                code=_EXIT_BUILD,
                hint=f"Tarball: {tarball}",
            )
        log.info(f"Verified release tarball: {tarball}")


if __name__ == "__main__":
    OpenSSLBuild.main()
