# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Base class for Nanvix consumer build scripts.

Consumer repositories subclass :class:`ZScript`, implement the lifecycle
hooks they need, and call ``ZScript.main()`` as the entry point::

    from nanvix_zutil import ZScript

    class MyBuild(ZScript):
        def build(self) -> None:
            self.run("make", "-f", "Makefile.nanvix", "all")

    if __name__ == "__main__":
        MyBuild.main()
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from nanvix_zutil import log
from nanvix_zutil.cli import build_parser
from nanvix_zutil.config import Config
from nanvix_zutil.exitcodes import EXIT_BUILD_FAILURE, EXIT_INVALID_ARGS


class ZScript:
    """Base class for consumer build scripts.

    Provides CLI dispatch, config management, subprocess execution, and
    structured logging.  Consumers subclass this and implement the lifecycle
    hooks they need.

    Attributes:
        SYSROOT_REQUIRED_FILES: Files that must exist in the sysroot
            regardless of deployment mode.  Override in subclasses to add
            library-specific files.
        SYSROOT_MULTI_PROCESS_FILES: Additional files required only for
            multi-process deployments (``linuxd.elf``, ``uservm.elf``).
        config: Persistent build configuration loaded from
            ``.nanvix/env.json`` and environment variables.
        repo_root: Absolute path to the consumer repository root.
        nanvix_dir: Absolute path to the ``.nanvix/`` directory.
        targets: Arguments passed after ``--`` on the command line.
            Lifecycle hooks can use these to customise behavior
            (e.g. ``./z test -- smoke integration``).
    """

    SYSROOT_REQUIRED_FILES: tuple[str, ...] = (
        "lib/libposix.a",
        "lib/user.ld",
        "bin/nanvixd.elf",
        "bin/kernel.elf",
        "bin/mkramfs.elf",
    )

    SYSROOT_MULTI_PROCESS_FILES: tuple[str, ...] = (
        "bin/linuxd.elf",
        "bin/uservm.elf",
    )

    def sysroot_required_files(self) -> list[str]:
        """Return the sysroot files required for the current deployment mode.

        Multi-process mode additionally requires ``linuxd.elf`` and
        ``uservm.elf``.  Subclasses can extend by overriding the class
        attributes or this method.
        """
        files = list(self.SYSROOT_REQUIRED_FILES)
        if self.config.deployment_mode == "multi-process":
            files.extend(self.SYSROOT_MULTI_PROCESS_FILES)
        return files

    def __init__(self, repo_root: Path) -> None:
        """Initialise ZScript for *repo_root*.

        Args:
            repo_root: Path to the consumer repository root.  Typically the
                directory that contains the ``.nanvix/`` folder.
        """
        self.repo_root = repo_root.resolve()
        self.nanvix_dir = self.repo_root / ".nanvix"
        self.config = Config(self.nanvix_dir)
        self.targets: list[str] = []

    # ------------------------------------------------------------------
    # Lifecycle hooks — override in subclass
    # ------------------------------------------------------------------

    def setup(self) -> None:
        """Prepare the build environment.

        Override to download sysroot / buildroot artifacts and persist
        their paths via :attr:`config`.
        """

    def build(self) -> None:
        """Build the project.

        Override to invoke the project's build system.
        """

    def test(self) -> None:
        """Run the project's test suite.

        Override to invoke the project's tests.
        """

    def benchmark(self) -> None:
        """Run the project's benchmarks.

        Override to invoke the project's benchmarks.
        """

    def release(self) -> None:
        """Package a release artifact.

        Override to produce the project's distribution archive.
        """

    def clean(self) -> None:
        """Remove build artifacts.

        Override to clean generated files.
        """

    # ------------------------------------------------------------------
    # Subprocess helper
    # ------------------------------------------------------------------

    def run(
        self,
        *args: str,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
    ) -> "subprocess.CompletedProcess[str]":
        """Run a subprocess, logging the command before execution.

        Args:
            *args: Command and arguments to execute.
            cwd: Working directory for the subprocess.  Defaults to
                :attr:`repo_root`.
            env: Environment variables for the subprocess.  ``None`` inherits
                the current process environment.

        Returns:
            The completed process result.

        Raises:
            SystemExit: With exit code ``5`` if the process exits with a
                non-zero status.
        """
        working_dir = cwd if cwd is not None else self.repo_root
        log.info(f"$ {' '.join(args)}")
        try:
            result = subprocess.run(
                list(args),
                cwd=working_dir,
                env=env,
                text=True,
                check=True,
            )
        except subprocess.CalledProcessError as exc:
            log.fatal(
                f"Command failed with exit code {exc.returncode}: {' '.join(args)}",
                code=EXIT_BUILD_FAILURE,
            )
        return result

    # ------------------------------------------------------------------
    # CLI entry point
    # ------------------------------------------------------------------

    @classmethod
    def main(cls) -> None:
        """Parse command-line arguments and dispatch to the appropriate
        lifecycle hook.

        Instantiates the class with the repository root inferred from the
        location of the calling script (``sys.argv[0]``).
        """
        parser = build_parser()

        # Split sys.argv on '--' to separate framework args from targets.
        argv = sys.argv[1:]
        if "--" in argv:
            sep = argv.index("--")
            framework_argv = argv[:sep]
            targets = argv[sep + 1 :]
        else:
            framework_argv = argv
            targets = []

        args = parser.parse_args(framework_argv)

        if args.json:
            log.set_json_mode(True)

        # Infer repo root: the parent of the .nanvix/ directory.
        script_path = Path(sys.argv[0]).resolve()
        # z.py lives at <repo>/.nanvix/z.py → repo_root is two levels up.
        if script_path.parent.name == ".nanvix":
            repo_root = script_path.parent.parent
        else:
            repo_root = script_path.parent

        instance = cls(repo_root)
        instance.targets = targets

        subcommand: str | None = args.subcommand

        if subcommand is None or subcommand == "help":
            parser.print_help()
            return

        dispatch: dict[str, object] = {
            "setup": instance.setup,
            "build": instance.build,
            "test": instance.test,
            "benchmark": instance.benchmark,
            "release": instance.release,
            "clean": instance.clean,
        }

        handler = dispatch.get(subcommand)
        if callable(handler):
            handler()
            log.success(f"{subcommand.capitalize()} complete")
        else:
            log.fatal(f"Unknown subcommand: {subcommand}", code=EXIT_INVALID_ARGS)
