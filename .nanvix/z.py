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

from nanvix_zutil import CFG_SYSROOT, CFG_TOOLCHAIN, EXIT_BUILD_FAILURE, EXIT_MISSING_DEP, ZScript, log  # type: ignore[reportAttributeAccessIssue, reportUnknownVariableType]

_EXIT_BUILD: int = EXIT_BUILD_FAILURE  # type: ignore[reportUnknownVariableType]
_EXIT_DEP: int = EXIT_MISSING_DEP  # type: ignore[reportUnknownVariableType]

# Makefile variable names (build-system-specific).
_MAKE_VAR_CONFIG = "CONFIG_NANVIX"
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
            self.docker = self.docker_config(derived)  # type: ignore[reportUnknownMemberType, reportAttributeAccessIssue]
            return

        # Check whether the base image already ships FindBin.
        if (
            subprocess.run(
                ["docker", "run", "--rm", base, "perl", "-MFindBin", "-e1"],  # type: ignore[reportUnknownArgumentType]
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
        self.docker = self.docker_config(derived)  # type: ignore[reportUnknownMemberType, reportAttributeAccessIssue]

    def _make_args(self, *targets: str, with_install_prefix: bool = True) -> list[str]:
        """Build the common make argument list."""
        sysroot = self.config.get(CFG_SYSROOT, "")
        if not sysroot:
            log.fatal(
                f"{CFG_SYSROOT} is not set.",
                code=_EXIT_DEP,
                hint="Run `./z setup` first to download the sysroot.",
            )
        toolchain = self.config.get(CFG_TOOLCHAIN, "/opt/nanvix") or "/opt/nanvix"
        sysroot_p = self.translate_path(Path(sysroot))
        toolchain_p = self.translate_path(Path(toolchain))

        args = [
            "make",
            "-f",
            "Makefile.nanvix",
            f"{_MAKE_VAR_CONFIG}=y",
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

    def setup(self) -> None:
        """Download the Nanvix sysroot."""
        super().setup()

    def build(self) -> None:
        """Cross-compile libcrypto.a, libssl.a, and test ELF (in Docker)."""
        self._ensure_docker_perl()
        # Build libraries and test binary in one pass.
        self.run(*self._make_args("all", _TEST_ELF), cwd=self.repo_root)

    def test(self) -> None:
        """Run tests natively on the host (no Docker).

        Three tiers:
          - smoke: verify build artifacts exist and look sane
          - integration: confirm the test ELF is a valid static binary
          - functional: execute the test ELF under nanvixd.elf
        """
        if IS_WINDOWS:
            self._run_tests_windows()
            return
        tier_map = {
            "test-smoke": self._test_smoke,
            "smoke": self._test_smoke,
            "test-integration": self._test_integration,
            "integration": self._test_integration,
            "test-functional": self._test_functional,
            "functional": self._test_functional,
        }
        run_all = [
            self._test_smoke,
            self._test_integration,
            self._test_functional,
        ]
        if self.targets:
            unknown = [t for t in self.targets if t not in tier_map and t != "test"]
            if unknown:
                log.fatal(
                    f"Unknown test target(s): {', '.join(unknown)}",
                    code=_EXIT_BUILD,
                    hint=f"Known: {', '.join(sorted(set(tier_map)))}",
                )
            if "test" in self.targets:
                tiers = run_all
            else:
                tiers = [tier_map[t] for t in self.targets]
        else:
            tiers = run_all
        for tier in tiers:
            tier()
        log.info("=== All openssl tests PASSED ===")

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
        self.run(
            "make",
            "-f",
            "Makefile.nanvix",
            "clean",
            cwd=self.repo_root,
        )

    # ------------------------------------------------------------------
    # Test tiers (run natively on the host)
    # ------------------------------------------------------------------

    def _test_smoke(self) -> None:
        """Verify build artefacts exist and look sane (no runtime)."""
        log.info("=== openssl smoke tests ===")
        repo = self.repo_root
        checks: list[tuple[Path, int]] = [
            (repo / "libcrypto.a", 1000),
            (repo / "libssl.a", 1000),
            (repo / "include" / "openssl" / "ssl.h", 0),
            (repo / "include" / "openssl" / "evp.h", 0),
            (repo / "include" / "openssl" / "opensslv.h", 0),
        ]
        for path, floor in checks:
            if not path.is_file():
                log.fatal(
                    f"smoke: missing {path.name}",
                    code=_EXIT_BUILD,
                    hint="Run `./z build` first.",
                )
            size = path.stat().st_size
            if size < floor:
                log.fatal(
                    f"smoke: {path.name} too small ({size} < {floor})",
                    code=_EXIT_BUILD,
                )
            log.info(f"  OK: {path.name} ({size} bytes)")
        log.info("  PASS: openssl smoke tests")

    def _test_integration(self) -> None:
        """Confirm the test ELF is a valid static binary."""
        log.info("=== openssl integration tests ===")
        elf = self.repo_root / _TEST_ELF
        if not elf.is_file():
            log.fatal(
                f"integration: {_TEST_ELF} not found",
                code=_EXIT_BUILD,
                hint="Run `./z build` first.",
            )
        with elf.open("rb") as fh:
            magic = fh.read(4)
        if magic != b"\x7fELF":
            log.fatal(
                f"integration: {_TEST_ELF} is not an ELF binary " f"(magic={magic!r})",
                code=_EXIT_BUILD,
            )
        size = elf.stat().st_size
        log.info(f"  OK: {_TEST_ELF} ({size} bytes, ELF)")
        log.info("  PASS: openssl integration tests")

    def _test_functional(self) -> None:
        """Run the test ELF under nanvixd.elf."""
        log.info("=== openssl functional tests ===")
        sysroot = self._sysroot_path()
        nanvixd = sysroot / "bin" / "nanvixd.elf"
        mkramfs = sysroot / "bin" / "mkramfs.elf"
        elf = self.repo_root / _TEST_ELF

        for tool in (nanvixd, mkramfs, elf):
            if not tool.is_file():
                log.fatal(
                    f"functional: {tool} not found",
                    code=_EXIT_DEP,
                    hint="Run `./z setup` and `./z build` first.",
                )

        with tempfile.TemporaryDirectory(prefix="openssl_test_") as tmp:
            tmp_path = Path(tmp)
            ramfs_dir = tmp_path / "ramfs"
            ramfs_dir.mkdir()
            (ramfs_dir / "tmp").mkdir()
            shutil.copy2(elf, ramfs_dir / elf.name)

            ramfs_img = tmp_path / "rootfs.img"
            subprocess.run(
                [str(mkramfs), "-o", str(ramfs_img), str(ramfs_dir)],
                check=True,
                timeout=60,
            )

            cmd = [
                str(nanvixd),
                "-bin-dir",
                str(sysroot / "bin"),
                "-ramfs",
                str(ramfs_img),
                "--",
                str(elf.resolve()),
            ]
            log.info(f"$ {' '.join(cmd)}")
            result = subprocess.run(
                cmd,
                timeout=180,
                capture_output=True,
                text=True,
            )

        if result.returncode != 0:
            sys.stdout.write(result.stdout)
            sys.stderr.write(result.stderr)
            log.fatal(
                f"functional: {_TEST_ELF} failed (exit {result.returncode})",
                code=_EXIT_BUILD,
            )
        sys.stdout.write(result.stdout)
        log.info(
            f"  PASS: openssl library test "
            f"{self.config.deployment_mode} (exit code 0)"
        )
        log.info("  PASS: openssl functional tests")

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
        """Run tests natively on Windows using nanvixd.exe."""
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
            with tempfile.TemporaryDirectory(prefix=f"nanvix_{name}_") as tmpdir:
                tmpdir_path = Path(tmpdir)
                ramfs_dir = tmpdir_path / "ramfs"
                ramfs_dir.mkdir()
                (ramfs_dir / "tmp").mkdir(exist_ok=True)
                shutil.copy2(binary, ramfs_dir / binary.name)
                ramfs_img = tmpdir_path / f"rootfs_{name}.img"
                try:
                    subprocess.run(
                        [
                            str(mkramfs.resolve()),
                            "-o",
                            str(ramfs_img),
                            str(ramfs_dir),
                        ],
                        check=True,
                        timeout=60,
                    )
                except subprocess.CalledProcessError as e:
                    print(f"FAIL {name} (mkramfs exit code {e.returncode})")
                    failed.append(name)
                    continue
                except subprocess.TimeoutExpired:
                    print(f"FAIL {name} (mkramfs timeout)")
                    failed.append(name)
                    continue
                try:
                    result = subprocess.run(
                        [
                            str(nanvixd.resolve()),
                            "-bin-dir",
                            str((sysroot_path / "bin").resolve()),
                            "-ramfs",
                            str(ramfs_img),
                            "--",
                            str(binary.resolve()),
                        ],
                        stdin=subprocess.DEVNULL,
                        timeout=120,
                    )
                    if result.returncode != 0:
                        print(f"FAIL {name} (exit code {result.returncode})")
                        failed.append(name)
                    else:
                        print(f"OK   {name}")
                except subprocess.TimeoutExpired:
                    print(f"FAIL {name} (timeout)")
                    failed.append(name)

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
                "release: tarball has no entries under" " sysroot/include/openssl/",
                code=_EXIT_BUILD,
                hint=f"Tarball: {tarball}",
            )
        log.info(f"Verified release tarball: {tarball}")


if __name__ == "__main__":
    OpenSSLBuild.main()
