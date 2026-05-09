# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Nanvix build script for OpenSSL.

Usage:
    ./z setup     # Download Nanvix sysroot
    ./z build     # Cross-compile libcrypto.a and libssl.a
    ./z test      # Run test suite (smoke + integration + functional)
    ./z release   # Package release tarball
    ./z clean     # Remove build artifacts
"""

import subprocess
import sys
from pathlib import Path

from nanvix_zutil import CFG_SYSROOT, CFG_TOOLCHAIN, EXIT_MISSING_DEP, ZScript, log

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
        if not self.docker:
            return

        base = self.docker.image
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
                "DEBIAN_FRONTEND=noninteractive apt-get install -y -qq perl bzip2 && "
                "rm -rf /var/lib/apt/lists/*\n"
            ),
            text=True,
            check=True,
        )
        self.docker = self.docker_config(derived)

    def _make_args(self, *targets: str, with_install_prefix: bool = True) -> list[str]:
        """Build the common make argument list."""
        sysroot = self.config.get(CFG_SYSROOT, "")
        if not sysroot:
            log.fatal(
                f"{CFG_SYSROOT} is not set.",
                code=EXIT_MISSING_DEP,
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

    def setup(self) -> None:
        """Download the Nanvix sysroot."""
        super().setup()

    def build(self) -> None:
        """Cross-compile libcrypto.a and libssl.a for Nanvix."""
        self._ensure_docker_perl()
        self.run(*self._make_args("all"), cwd=self.repo_root)

    def test(self) -> None:
        """Run the test suite.

        On non-Windows, delegates to the Makefile (smoke + integration + functional).
        On Windows, runs test binaries from build/ via nanvixd.exe natively,
        following the same pattern as posix-tests and cpython.
        """
        if IS_WINDOWS:
            self._run_tests_windows()
            return
        self._ensure_docker_perl()
        targets = self.targets if self.targets else ["test"]
        self.run(*self._make_args(*targets), cwd=self.repo_root)

    def _run_tests_windows(self) -> None:
        """Run tests natively on Windows using nanvixd.exe."""
        if self.config.deployment_mode != "standalone":
            print(
                f"Skipping tests on Windows for mode '{self.config.deployment_mode}'"
                " (single-process and multi-process require linuxd, Linux only)."
            )
            return
        sysroot = self.config.get(CFG_SYSROOT, "")
        if not sysroot:
            log.fatal(
                f"{CFG_SYSROOT} is not set.",
                code=EXIT_MISSING_DEP,
                hint="Run `./z setup` first.",
            )
        sysroot_path = Path(sysroot)
        nanvixd = sysroot_path / "bin" / "nanvixd.exe"
        mkramfs = sysroot_path / "bin" / "mkramfs.exe"
        if not nanvixd.is_file():
            log.fatal(
                "nanvixd.exe not found.",
                code=EXIT_MISSING_DEP,
                hint="Run `./z setup` first.",
            )
        if not mkramfs.is_file():
            log.fatal(
                "mkramfs.exe not found.",
                code=EXIT_MISSING_DEP,
                hint="Run `./z setup` first.",
            )

        build_dir = self.repo_root / "build"
        test_binaries = sorted(build_dir.glob("*.elf")) if build_dir.is_dir() else []

        if not test_binaries:
            print("No test binaries found in build/ -- smoke test only.")
            print("OK: library-only repo, no functional tests to run on Windows")
            return

        import shutil
        import tempfile

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
                # Write ramfs image alongside the ramfs source dir to avoid
                # self-inclusion while keeping artifacts scoped to this temp dir.
                ramfs_img = tmpdir_path / f"rootfs_{name}.img"
                try:
                    subprocess.run(
                        [str(mkramfs.resolve()), "-o", str(ramfs_img), str(ramfs_dir)],
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

    def release(self) -> None:
        """Package the OpenSSL release tarball and verify it."""
        self._ensure_docker_perl()
        self.run(*self._make_args("package"), cwd=self.repo_root)
        self.run(*self._make_args("verify-package"), cwd=self.repo_root)

    def clean(self) -> None:
        """Remove build artifacts."""
        self.run(
            "make",
            "-f",
            "Makefile.nanvix",
            "clean",
            cwd=self.repo_root,
        )


if __name__ == "__main__":
    OpenSSLBuild.main()
