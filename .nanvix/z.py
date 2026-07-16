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
    dev_out,
    dist_dir,
    nanvix_root,
    out_dir,
    repo_root,
    test_out,
)

_EXIT_BUILD: int = EXIT_BUILD_FAILURE
_EXIT_DEP: int = EXIT_MISSING_DEP

# Makefile variable names (build-system-specific).
_MAKE_VAR_TOOLCHAIN = "NANVIX_TOOLCHAIN"
_MAKE_VAR_PLATFORM = "PLATFORM"
_MAKE_VAR_PROCESS_MODE = "PROCESS_MODE"
_MAKE_VAR_MEMORY_SIZE = "MEMORY_SIZE"
_MAKE_VAR_INSTALL_PREFIX = "INSTALL_PREFIX"

# OpenSSL embeds --prefix into compiled artifacts (OPENSSLDIR, etc.).
# Use /sysroot so that release tarballs don't contain ephemeral runner paths.
_DEFAULT_INSTALL_PREFIX = "/sysroot"

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
# must be copied back to the host workspace.  The test ELF is kept at the
# repo root post-build so ``_require_build_artifacts`` finds it without
# depending on the install-staged copy.  Release-staged artifacts
# (libraries, headers, test ELF copy) are covered by
# ``_staged_output_files()``.
_BUILD_OUTPUTS: list[str] = [
    _TEST_ELF,
]

IS_WINDOWS = sys.platform == "win32"


class OpenSSLBuild(ZScript):
    """Build script for nanvix/openssl."""

    # Build-time headers, libraries, startup objects, and linker scripts come
    # from the SDK. The downloaded sysroot is used only to run tests.
    SYSROOT_REQUIRED_FILES = (
        "bin/nanvixd.elf",
        "bin/kernel.elf",
        "bin/mkramfs.elf",
    )
    SYSROOT_REQUIRED_FILES_WINDOWS = (
        "bin/nanvixd.exe",
        "bin/kernel.elf",
        "bin/mkramfs.exe",
    )

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
            str((dev_out() / "lib" / "libcrypto.a").relative_to(root)),
            str((dev_out() / "lib" / "libssl.a").relative_to(root)),
            str((test_out() / _TEST_ELF).relative_to(root)),
        ]
        staged.extend(
            str((dev_out() / "include" / "openssl" / h).relative_to(root))
            for h in _GENERATED_HEADERS
        )
        return staged

    def _make_args(
        self,
        *targets: str,
        with_install_prefix: bool = True,
    ) -> list[str]:
        """Build the common make argument list.

        ``NANVIX_TOOLCHAIN`` is always the in-container SDK path because
        the only goals that dereference it run under Docker.
        """
        toolchain_p = str(TOOLCHAIN_CONTAINER_PATH)

        def translate(p: Path):
            return self.docker.translate_path(p) if self.docker else p

        args = [
            "make",
            "-f",
            "Makefile.nanvix",
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
                f"LIB_OUT={translate(dev_out() / 'lib')}",
                f"INCLUDE_OUT={translate(dev_out() / 'include')}",
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

        On windows-ci the workflow downloads test artefacts into
        ``test_out()`` (the windows-test job has no preceding `./z build`
        on the Windows runner), so we accept either location.
        """
        for candidate in (test_out() / _TEST_ELF, repo_root() / _TEST_ELF):
            if candidate.is_file():
                return
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
        # Source _TEST_ELF from `test_out()` (the windows-ci artifact
        # overlay location, populated by `_stage_artifacts_elf_so` in
        # nanvix_scripts and by the canonical workflow's download-artifact
        # step at `.nanvix/out/test/`) with repo_root() as legacy fallback.
        elf_src: Path | None = None
        for candidate in (test_out() / _TEST_ELF, repo_root() / _TEST_ELF):
            if candidate.is_file():
                elf_src = candidate
                break
        if elf_src is None:
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

        initrd = make_initrd(elf_src, test_out())
        try:
            test_out().mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(
                prefix="openssl_test_",
                dir=test_out(),
            ) as tmp:
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
