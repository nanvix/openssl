# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Nanvix build script for OpenSSL.

Usage:
    ./z setup     # Download Nanvix sysroot
    ./z build     # Cross-compile libcrypto.a, libssl.a, and test ELF
    ./z test      # Run functional test (test ELF on nanvixd)
    ./z release   # Package release tarball
    ./z clean     # Remove build artifacts
"""

from __future__ import annotations

import dataclasses
import subprocess
import sys
import tempfile
from pathlib import Path

from nanvix_zutil import (
    CFG_SYSROOT,
    EXIT_BUILD_FAILURE,
    EXIT_MISSING_DEP,
    TOOLCHAIN_CONTAINER_PATH,
    DockerConfig,
    ZScript,
    log,
    make_initrd,
    run,
)
from nanvix_zutil.paths import (
    dist_dir,
    include_out,
    lib_out,
    nanvix_root,
    out_dir,
    repo_root,
    test_out,
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

# Public headers generated from ``include/openssl/*.h.in`` templates by
# OpenSSL's ``Configure`` step.  Listed for documentation and for
# inclusion in the install-staged release tree (the install rule globs
# ``include/openssl/*.h`` so the list is informational here).
_GENERATED_HEADERS: list[str] = [
    "asn1.h",
    "asn1t.h",
    "bio.h",
    "cmp.h",
    "cms.h",
    "comp.h",
    "conf.h",
    "configuration.h",
    "core_names.h",
    "crmf.h",
    "crypto.h",
    "ct.h",
    "err.h",
    "ess.h",
    "fipskey.h",
    "lhash.h",
    "ocsp.h",
    "opensslv.h",
    "pkcs12.h",
    "pkcs7.h",
    "safestack.h",
    "srp.h",
    "ssl.h",
    "ui.h",
    "x509.h",
    "x509_acert.h",
    "x509_vfy.h",
    "x509v3.h",
]

# Files produced inside the Docker tar-copy build dir (Windows path) that
# must be copied back to the host workspace.  Only the test ELF is needed
# at the repo root post-build: it is the test target's input and is
# resolved by ``make_initrd`` relative to ``repo_root()``.  Release-staged
# artifacts (libraries, headers, test ELF copy) are covered by
# ``_staged_output_files()``.
_BUILD_OUTPUTS: list[str] = [
    _TEST_ELF,
]

IS_WINDOWS = sys.platform == "win32"


class OpenSSLBuild(ZScript):
    """Build script for nanvix/openssl."""

    def docker_image(self) -> str:
        """Return the Docker image for cross-compilation."""
        return _DOCKER_IMAGE

    def docker_config(self, image: str) -> DockerConfig:
        """Extend the default Docker config with build outputs.

        On Windows the build runs in a container-local directory via
        :meth:`DockerConfig.build_windows_run_cmd` to avoid VirtioFS I/O
        penalties; ``output_files`` tells that helper which artefacts to
        copy back into the mounted workspace so host-side test code can
        find them. The list is harmless on Linux (where the workspace
        itself is the build directory).
        """
        cfg = super().docker_config(image)
        return dataclasses.replace(
            cfg,
            output_files=list(_BUILD_OUTPUTS) + self._staged_output_files(),
        )

    def _staged_output_files(self) -> list[str]:
        """Return install-staged artifact paths (relative to repo_root())
        so Windows tar-copy mode also copies them back to the host.
        """
        root = repo_root()
        staged: list[str] = [
            str((lib_out() / "libcrypto.a").relative_to(root)),
            str((lib_out() / "libssl.a").relative_to(root)),
            str((test_out() / _TEST_ELF).relative_to(root)),
        ]
        staged.extend(
            str((include_out() / "openssl" / h).relative_to(root))
            for h in _GENERATED_HEADERS
        )
        return staged

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

        def translate(p: Path):
            return self.docker.translate_path(p) if self.docker else p

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
                f"NANVIX_ROOT={translate(nanvix_root())}",
                f"OUT_DIR={translate(out_dir())}",
                f"DIST_DIR={translate(dist_dir())}",
                f"LIB_OUT={translate(lib_out())}",
                f"INCLUDE_OUT={translate(include_out())}",
                f"TEST_OUT={translate(test_out())}",
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
            cwd=repo_root(),
            docker=self.docker,
        )

    def test(self) -> None:
        """Run the functional test (test ELF on ``nanvixd``).

        Boots the cross-compiled ``openssl_nanvix_test.elf`` under
        ``nanvixd`` and asserts the in-guest OpenSSL self-test prints
        ``PASS``.

        Only standalone deployment mode runs a real test; other modes
        require ``linuxd`` (Linux only) and are not yet wired up here.
        """
        if self.config.deployment_mode != "standalone":
            log.info(
                f"Skipping tests for mode '{self.config.deployment_mode}'"
                " (only standalone is supported)."
            )
            return

        self._require_build_artifacts()
        self._run_functional_standalone()

    def clean(self) -> None:
        """Remove build artifacts."""
        run(
            "make",
            "-f",
            "Makefile.nanvix",
            "clean",
            cwd=repo_root(),
        )

    # ------------------------------------------------------------------
    # Preflight
    # ------------------------------------------------------------------

    def _require_build_artifacts(self) -> None:
        """Fatal-out early when the test ELF isn't present.

        The Makefile recipes will catch this too, but emitting it from
        Python keeps the failure mode consistent across platforms/modes
        (Linux/Windows, microvm/standalone) and points the user at the
        right remediation without needing to parse make output.
        """
        elf = repo_root() / _TEST_ELF
        if not elf.is_file():
            log.fatal(
                f"test: missing build artefact: {_TEST_ELF}",
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
        elf = repo_root() / _TEST_ELF
        if not elf.is_file():
            log.fatal(
                f"{_TEST_ELF} not found.",
                code=_EXIT_BUILD,
                hint="Run `./z build` first.",
            )

        sysroot = self._sysroot_path()
        # Host tools are .exe on Windows, .elf elsewhere. The guest test
        # binary always keeps its .elf suffix because nanvixd loads it.
        host_ext = ".exe" if IS_WINDOWS else ".elf"
        mkramfs = sysroot / "bin" / f"mkramfs{host_ext}"
        nanvixd = sysroot / "bin" / f"nanvixd{host_ext}"
        for tool in (mkramfs, nanvixd):
            if not tool.is_file():
                log.fatal(
                    f"functional: {tool} not found",
                    code=_EXIT_DEP,
                    hint="Run `./z setup` first.",
                )

        log.info("=== openssl functional tests ===")
        log.info(f"  Running {_TEST_ELF} via nanvixd standalone...")

        initrd = make_initrd(self, _TEST_ELF, test=True)
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


if __name__ == "__main__":
    OpenSSLBuild.main()
