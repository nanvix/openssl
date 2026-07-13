# OpenSSL Port for Nanvix

> **TL;DR:** This is a port of the OpenSSL cryptographic library for the Nanvix operating system. Jump to [Quick Start](#quick-start) to get started immediately.

---

## Overview

This document describes the port of [OpenSSL](https://www.openssl.org/) cryptographic library for the [Nanvix](https://github.com/nanvix/nanvix) operating system. This port enables OpenSSL to run on Nanvix, a POSIX-compatible educational operating system.

| Property | Value |
|----------|-------|
| **Base Version** | OpenSSL 3.5.0 |
| **Target Platform** | Nanvix (i686) |
| **Build System** | GNU Make (wrapping OpenSSL Configure) |

**What's included:**
- ✅ Cross-compilation support for Nanvix
- ✅ Static library builds (`libcrypto.a`, `libssl.a`)
- ✅ Build helper scripts
- ✅ CI/CD integration
- ✅ Test executables (`openssl_nanvix_test.elf`)

---

## Table of Contents

1. [Quick Start](#quick-start)
2. [Prerequisites](#prerequisites)
3. [Building](#building)
4. [Testing](#testing)
5. [Changes Summary](#changes-summary)
6. [Known Limitations](#known-limitations)
7. [CI/CD](#cicd)

---

## Quick Start

For experienced users who want to build quickly:

```bash
# 1. Install nanvix-zutil (requires gh CLI: https://cli.github.com)
#    Using a venv is recommended on modern Linux distros (PEP 668).
python3 -m venv .venv && source .venv/bin/activate
WHEEL_URL=$(gh api repos/nanvix/zutils/releases/latest \
  --jq '.assets[] | select(.name | endswith(".whl")) | .browser_download_url')
pip install "$WHEEL_URL"

# 2. Setup (downloads Nanvix sysroot automatically)
./z setup

# 3. Build
./z build

# 4. Run tests
./z test
```

Continue reading for detailed instructions.

---

## Prerequisites

You need the following to build OpenSSL for Nanvix:

| Component | Description | Install |
|-----------|-------------|---------|
| **nanvix-zutil** | Build orchestration CLI | `pip install` from [GitHub Releases](https://github.com/nanvix/zutils/releases) |
| **Nanvix SDK** | Clang/LLVM cross-compilation SDK | Pinned Docker image or native install |
| **Nanvix Sysroot** | Runtime binaries used by tests | `nanvix-zutil setup` |

### Available Platform Configurations

| Platform | Process Mode | Artifact Pattern |
|----------|--------------|------------------|
| microvm | standalone | `microvm.*standalone` |

### Downloading Nanvix

```bash
curl -fsSL https://raw.githubusercontent.com/nanvix/nanvix/refs/heads/dev/scripts/get-nanvix.sh | bash -s -- nanvix-artifacts
```

The script downloads all release artifacts. Extract the one matching your target platform (see [Quick Start](#quick-start) for a complete example).

---

## Building

### Using nanvix-zutil (Recommended)

```bash
# Install nanvix-zutil (use a venv on modern Linux distros)
python3 -m venv .venv && source .venv/bin/activate
WHEEL_URL=$(gh api repos/nanvix/zutils/releases/latest \
  --jq '.assets[] | select(.name | endswith(".whl")) | .browser_download_url')
pip install "$WHEEL_URL"

# Setup the runtime sysroot and manifest-pinned SDK image.
./z setup
./z build
```

### Invoking Make Directly (Advanced)

`./z build` is the supported entrypoint: it manages the Docker SDK image and host/container artefact copy-back. `Makefile.nanvix` can also be invoked directly, but the caller is responsible for providing the Nanvix SDK:

```bash
export NANVIX_TOOLCHAIN=/path/to/sdk  # Contains: nanvix-sdk.json and bin/clang
make -f Makefile.nanvix \
  PLATFORM=microvm PROCESS_MODE=standalone MEMORY_SIZE=256mb \
  NANVIX_TOOLCHAIN="$NANVIX_TOOLCHAIN" all
```

The SDK Clang driver selects the Nanvix headers, libc, compiler runtime,
startup objects, and linker script. The downloaded sysroot is runtime-only.
The manifest-pinned SDK also includes Perl and `FindBin`, so no derived OpenSSL
builder image is needed.

### Build Outputs

After a successful build, you will have:

| File | Description |
|------|-------------|
| `libcrypto.a` | OpenSSL cryptography static library |
| `libssl.a` | OpenSSL SSL/TLS static library |
| `providers/libcommon.a` | Common provider library |
| `providers/libdefault.a` | Default provider library |
| `providers/liblegacy.a` | Legacy provider library |

---

## Testing

> **Important:** OpenSSL is built without command-line applications (`no-apps`). Testing boots a cross-compiled test ELF under `nanvixd` and verifies the static libraries link and run in-guest.

### Running the Test Suite

```bash
./z test
```

### Test Coverage

The test target runs the functional test: it boots the cross-compiled
`openssl_nanvix_test.elf` under `nanvixd` and asserts the in-guest
OpenSSL self-test prints `PASS` (covers `libcrypto`/`libssl` linkage,
SHA-256, and BIGNUM).

---

## Changes Summary

The following changes were made to support Nanvix.

### Build System Changes

| Change | Description |
|--------|-------------|
| New Makefile | Added `Makefile.nanvix` for Nanvix cross-compilation |
| Cross-compilation | Driven by `PLATFORM`, `PROCESS_MODE`, `MEMORY_SIZE`, `NANVIX_TOOLCHAIN` |
| Docker support | Cross-compile container orchestrated by `z.py` (image cached, artefacts copied back) |
| SDK toolchain | `/opt/nanvix/bin/clang`, `clang++`, `llvm-ar`, and `llvm-ranlib` |
| Configure wrapper | Wraps `./Configure` with Nanvix cross-compilation settings |
| Shared libraries | Disabled (`no-shared`) |
| Applications | Disabled (`no-apps`) for library-only build |

### Platform Configuration

A Nanvix platform target is defined in `Configurations/10-main.conf`:

```perl
"nanvix" => {
    inherit_from     => [ "BASE_unix" ],
    asm_arch         => 'x86',
    perlasm_scheme   => "elf",
    cppflags         => add(threads("-D_REENTRANT")),
    thread_scheme    => "pthreads",
},
```

### Configure Options

| Option | Description |
|--------|-------------|
| `no-shared` | Build static libraries only |
| `threads` | Enable threading support |
| `no-dso` | Disable dynamic shared objects |
| `no-apps` | Don't build command-line applications |
| `no-docs` | Don't build documentation |
| `no-rdrand` | Disable RDRAND instruction (not available on Nanvix) |
| `no-posix-io` | Disable POSIX I/O operations |
| `no-asm` | Disable assembly optimizations |
| `no-ui-console` | Disable console UI |

### New Files

| File | Purpose |
|------|---------|
| `Makefile.nanvix` | Standalone Makefile for Nanvix cross-compilation |
| `NANVIX.md` | This documentation file |
| `z` | Unified entry point (delegates to `z.sh` or `z.ps1`) |
| `z.sh` | Bash wrapper that delegates to `nanvix-zutil` CLI |
| `z.ps1` | PowerShell wrapper that delegates to `nanvix-zutil` CLI |
| `.nanvix/z.py` | Build script (extends `nanvix-zutil` `ZScript`) |
| `.nanvix/nanvix.toml` | Package manifest for dependency resolution |
| `.github/workflows/nanvix-ci.yml` | CI workflow for automated builds |

### Legacy Build Script

The `z` script delegates to `nanvix-zutil`, which orchestrates the entire build lifecycle (setup, build, test, release, clean). Users should prefer `./z` over raw `make -f Makefile.nanvix` invocations for consistency with other Nanvix ports.

---

## Known Limitations

| Limitation | Impact |
|------------|--------|
| **No shared libraries** | Only static libraries (`libcrypto.a`, `libssl.a`) are built |
| **No command-line tools** | `openssl` CLI not available (`no-apps`) |
| **No assembly optimizations** | Pure C implementation (`no-asm`) |
| **No RDRAND** | Hardware random number generator not available |
| **No POSIX I/O** | File operations limited |
| **Static linking only** | All applications using OpenSSL must be statically linked |

---

## CI/CD

The GitHub Actions workflow at `.github/workflows/nanvix-ci.yml` automates building and testing on every change. It uses the `nanvix-zutil` CLI (installed from the wheel in GitHub Releases) for all build orchestration.

### Workflow Structure

| Job | Description |
|-----|-------------|
| `ci` | Calls the shared Nanvix reusable workflow that builds, tests, and packages this port |

The `ci` job delegates to a central reusable workflow, which defines internal jobs such as
`get-nanvix-info`, `build`, `release`, and `report-failure` to handle manifest resolution,
cross-compilation, release creation, and failure reporting. These internal jobs live in the
shared CI configuration and are not defined directly in this repository's workflow file.

### Trigger Events

| Event | Description |
|-------|-------------|
| Push to `nanvix/**` | Any push to Nanvix branches |
| PR to `nanvix/**` | Pull requests targeting Nanvix branches |
| Daily schedule | Runs at midnight UTC |
| Manual dispatch | Can be triggered manually |

### Build Matrix

PR CI runs the microvm standalone configuration supported by the Nanvix
0.20.0 runtime release:

| Platform | Process Mode | Memory Size | Linux Build & Test | Windows Test |
|----------|--------------|-------------|-------------------|--------------|
| microvm | standalone | 256mb | ✅ | ✅ |

Nanvix 0.20.0 publishes only microvm runtime assets and does not publish a
standalone 128mb asset, so the SDK migration narrows the matrix to microvm at
256mb. All existing Linux and Windows test types remain enabled.

---
