# Bootstrap wrapper for Nanvix build scripts.
# Modeled on https://github.com/rust-lang/rust/blob/main/x.ps1

# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

# If script execution is disabled, run:
#   Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser

#Requires -Version 7.0

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$MIN_MAJOR = 3
$MIN_MINOR = 12

# nanvix-zutil release to install in the project venv.
$ZUTIL_TAG = "v0.2.0"
$ZUTIL_RELEASE_BASE = "https://github.com/nanvix/zutils/releases/download"
$ZUTIL_HASH = "sha256:2d777cd93573e28bdfbfc978193a970b904fe564b82e17207b864a1221c7f0ca"

function Find-Python {
    # Prefer version-suffixed interpreters (e.g., python3.12, python3.13) first,
    # then fall back to generic names.
    $candidates = @()
    for ($minor = 20; $minor -ge $MIN_MINOR; $minor--) {
        $candidates += "python3.$minor"
    }
    $candidates += @("python3", "python")
    foreach ($candidate in $candidates) {
        $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($null -eq $cmd) { continue }

        try {
            $version = & $candidate -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
            if ($LASTEXITCODE -ne 0) { continue }
            $parts = $version.Trim().Split(".")
            $major = [int]$parts[0]
            $minor = [int]$parts[1]
            if ($major -gt $MIN_MAJOR -or ($major -eq $MIN_MAJOR -and $minor -ge $MIN_MINOR)) {
                return $candidate
            }
        } catch {
            continue
        }
    }

    # Windows 'py' launcher: probe specific minor versions (high to low).
    $pyCmd = Get-Command "py" -ErrorAction SilentlyContinue
    if ($null -ne $pyCmd) {
        for ($minor = 20; $minor -ge $MIN_MINOR; $minor--) {
            try {
                $exe = & py "-3.$minor" -c "import sys; print(sys.executable)" 2>$null
                if ($LASTEXITCODE -eq 0 -and $exe) {
                    return $exe.Trim()
                }
            } catch {
                continue
            }
        }
    }

    return $null
}

# Run the discovered Python interpreter.
function Invoke-Python {
    param([Parameter(ValueFromRemainingArguments)] [string[]]$Args_)
    & $python @Args_
}

$python = Find-Python
if ($null -eq $python) {
    Write-Warning "error: Python ${MIN_MAJOR}.${MIN_MINOR}+ not found in PATH."
    Write-Host "hint:  Install Python 3.12+ and ensure it is on your PATH." -ForegroundColor Yellow
    exit 3
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$zScript = Join-Path $scriptDir ".nanvix" "z.py"
$venvDir = Join-Path $scriptDir ".nanvix" "venv"
if ($IsWindows) {
    $venvPython = Join-Path $venvDir "Scripts" "python.exe"
} else {
    $venvPython = Join-Path $venvDir "bin" "python"
}

if (-not (Test-Path $zScript)) {
    Write-Warning "error: $zScript not found."
    Write-Host "hint:  Create .nanvix/z.py with a ZScript subclass." -ForegroundColor Yellow
    exit 3
}

# Bootstrap: create venv and install nanvix-zutil if needed.
if (-not (Test-Path $venvPython)) {
    Write-Host "bootstrap: creating venv …" -ForegroundColor Cyan
    Invoke-Python -m venv $venvDir

    $zutilPath = $env:NANVIX_ZUTIL_PATH
    if ($zutilPath) {
        Write-Host "bootstrap: installing nanvix-zutil (editable) …" -ForegroundColor Cyan
        & $venvPython -m pip install -q -e $zutilPath
    } else {
        $version = $ZUTIL_TAG.TrimStart("v").Replace("-", "")
        $whlName = "nanvix_zutil-${version}-py3-none-any.whl"
        $whlUrl = "${ZUTIL_RELEASE_BASE}/${ZUTIL_TAG}/${whlName}"
        Write-Host "bootstrap: installing nanvix-zutil ($ZUTIL_TAG) …" -ForegroundColor Cyan
        $tmpReq = [System.IO.Path]::GetTempFileName()
        Set-Content -Path $tmpReq -Value "nanvix_zutil @ $whlUrl --hash=$ZUTIL_HASH"
        try {
            & $venvPython -m pip install -q --require-hashes -r $tmpReq
        } finally {
            Remove-Item -Path $tmpReq -ErrorAction SilentlyContinue
        }
    }
}

& $venvPython $zScript @args
exit $LASTEXITCODE
