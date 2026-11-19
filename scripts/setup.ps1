<#
.SYNOPSIS
  One-time developer set-up for DevTeam AI on Windows (roadmap §13 Part A).

.DESCRIPTION
  Installs the locked Python and npm dependencies, then runs the environment doctor.
  It does not install system tools, change environment variables or touch Git hooks unless asked.

.PARAMETER InstallHooks
  Also install the pre-commit hooks into .git/hooks.
#>
[CmdletBinding()]
param(
    [switch]$InstallHooks
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    Write-Host "==> uv sync --locked"
    uv sync --locked
    if ($LASTEXITCODE -ne 0) { throw "uv sync failed" }

    Write-Host "==> npm ci (apps/web)"
    npm --prefix apps/web ci
    if ($LASTEXITCODE -ne 0) { throw "npm ci failed" }

    if ($InstallHooks) {
        Write-Host "==> pre-commit install"
        uv run pre-commit install
        if ($LASTEXITCODE -ne 0) { throw "pre-commit install failed" }
    }

    Write-Host "==> environment doctor"
    uv run python scripts/doctor.py
    if ($LASTEXITCODE -ne 0) { throw "doctor reported failures" }
}
finally {
    Pop-Location
}
