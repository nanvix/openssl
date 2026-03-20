# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Nanvix build script for openssl.

Usage (from repository root):
    ./z setup      # Download sysroot
    ./z build      # Build libcrypto.a and libssl.a
    ./z test       # Run smoke, integration, and functional tests
    ./z release    # Package release tarball
    ./z clean      # Remove build artifacts
"""

from nanvix_zutil import CFG_GH_TOKEN, CFG_SYSROOT, EXIT_MISSING_DEP, Sysroot, ZScript, log


class OpenSSLBuild(ZScript):
    """Build script for nanvix/openssl."""

    def _make(self, *targets: str, extra_vars: dict[str, str] | None = None) -> None:
        """Run ``make -f Makefile.nanvix`` with standard Nanvix variables."""
        nanvix_sysroot = self.config.get(CFG_SYSROOT, "")
        if not nanvix_sysroot:
            log.fatal(
                f"{CFG_SYSROOT} is not set.",
                code=EXIT_MISSING_DEP,
                hint="Run `./z setup` first to download the sysroot.",
            )

        cmd: list[str] = [
            "make",
            "-f",
            "Makefile.nanvix",
            f"CONFIG_NANVIX=y",
            f"NANVIX_HOME={nanvix_sysroot}",
        ]
        if extra_vars:
            for key, val in extra_vars.items():
                cmd.append(f"{key}={val}")
        cmd.extend(targets)
        self.run(*cmd, cwd=self.repo_root)

    def setup(self) -> None:
        """Download the Nanvix sysroot and persist its path."""
        sysroot = Sysroot.download(
            machine=self.config.machine,
            deployment_mode=self.config.deployment_mode,
            memory_size=self.config.memory_size,
            tag="latest",
            gh_token=self.config.get(CFG_GH_TOKEN),
        )
        sysroot.verify(list(self.SYSROOT_REQUIRED_FILES))
        self.config.set(CFG_SYSROOT, str(sysroot.path))
        self.config.save()

    def build(self) -> None:
        """Build libcrypto.a and libssl.a."""
        self._make("all")

    def test(self) -> None:
        """Run smoke, integration, and functional tests."""
        platform_vars = {
            "PLATFORM": self.config.machine,
            "PROCESS_MODE": self.config.deployment_mode,
            "MEMORY_SIZE": self.config.memory_size,
        }
        self._make("test", extra_vars=platform_vars)

    def release(self) -> None:
        """Package the release tarball and verify it."""
        platform_vars = {
            "PLATFORM": self.config.machine,
            "PROCESS_MODE": self.config.deployment_mode,
            "MEMORY_SIZE": self.config.memory_size,
        }
        self._make("package", extra_vars=platform_vars)
        self._make("verify-package", extra_vars=platform_vars)

    def clean(self) -> None:
        """Remove build artifacts."""
        self.run("make", "-f", "Makefile.nanvix", "clean", cwd=self.repo_root)


if __name__ == "__main__":
    OpenSSLBuild.main()
