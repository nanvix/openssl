# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Nanvix build script for openssl."""

from nanvix_zutil import CFG_SYSROOT, CFG_TOOLCHAIN, EXIT_BUILD_FAILURE, ZScript, log


class OpensslBuild(ZScript):
    """Build script for nanvix/openssl."""

    def _make_args(self, *targets: str) -> list[str]:
        """Build the common make argument list."""
        sysroot = self.config.get(CFG_SYSROOT, "")
        if not sysroot:
            log.fatal(
                "Sysroot not configured \u2014 run 'nanvix-zutil setup' first.",
                code=EXIT_BUILD_FAILURE,
            )
        toolchain = self.config.get(CFG_TOOLCHAIN, "/opt/nanvix") or "/opt/nanvix"

        args = [
            "make", "-f", "Makefile.nanvix",
            "CONFIG_NANVIX=y",
            f"NANVIX_HOME={sysroot}",
            f"NANVIX_TOOLCHAIN={toolchain}",
            f"PLATFORM={self.config.machine}",
            f"PROCESS_MODE={self.config.deployment_mode}",
            f"MEMORY_SIZE={self.config.memory_size}",
        ]
        args.extend(targets)
        return args

    def build(self) -> None:
        """Cross-compile libcrypto.a and libssl.a for Nanvix."""
        self.run(*self._make_args("all"))

    def test(self) -> None:
        """Run the openssl test suite."""
        targets = self.targets if self.targets else ["test"]
        self.run(*self._make_args(*targets))

    def release(self) -> None:
        """Package the openssl release tarball and verify it."""
        self.run(*self._make_args("package"))
        self.run(*self._make_args("verify-package"))

    def clean(self) -> None:
        """Remove build artifacts."""
        self.run("make", "-f", "Makefile.nanvix", "clean")


if __name__ == "__main__":
    OpensslBuild.main()
