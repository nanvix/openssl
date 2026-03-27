# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Standard exit codes for the Nanvix build ecosystem.

Every consumer of ``nanvix_zutil`` should use these constants instead of
bare integer literals when calling :func:`nanvix_zutil.log.fatal` or
:func:`sys.exit`.
"""

EXIT_SUCCESS: int = 0
"""The operation completed successfully."""

EXIT_GENERAL_ERROR: int = 1
"""An unspecified error occurred."""

EXIT_INVALID_ARGS: int = 2
"""The command was invoked with invalid arguments."""

EXIT_MISSING_DEP: int = 3
"""A required dependency (tool, library, sysroot, etc.) is missing."""

EXIT_NETWORK_ERROR: int = 4
"""A network operation (download, API call, etc.) failed."""

EXIT_BUILD_FAILURE: int = 5
"""A build step (compilation, linking, etc.) failed."""

EXIT_TEST_FAILURE: int = 6
"""One or more tests failed."""
