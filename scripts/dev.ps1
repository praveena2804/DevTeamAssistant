<#
.SYNOPSIS
  Start the DevTeam AI API and web UI for local development (both bound to 127.0.0.1).

.DESCRIPTION
  API:  http://127.0.0.1:8000  (uv run python -m devteam_api)
  UI:   http://127.0.0.1:5173  (Vite dev server; proxies /health, /version and /api to the API)
  Press Ctrl+C to stop both processes.

.PARAMETER Smoke
  Start both processes, verify /health directly and through the UI proxy, then stop (used as a smoke test).
#>
[CmdletBinding()]
param(
    [switch]$Smoke
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$api = $null
$web = $null

function Wait-Http([string]$Url, [int]$TimeoutSeconds = 60) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            return Invoke-RestMethod -Uri $Url -TimeoutSec 3
        }
        catch {
            Start-Sleep -Milliseconds 500
        }
    }
    throw "Timed out waiting for $Url"
}

try {
    $api = Start-Process -FilePath "uv" -ArgumentList @("run", "python", "-m", "devteam_api") `
        -WorkingDirectory $root -PassThru -NoNewWindow
    $web = Start-Process -FilePath "npm.cmd" -ArgumentList @("--prefix", "apps/web", "run", "dev") `
        -WorkingDirectory $root -PassThru -NoNewWindow

    $direct = Wait-Http "http://127.0.0.1:8000/health"
    Write-Host "API health: $($direct.status)"
    $proxied = Wait-Http "http://127.0.0.1:5173/health"
    Write-Host "UI proxy health: $($proxied.status)"

    if ($Smoke) {
        if ($direct.status -ne "ok" -or $proxied.status -ne "ok") { throw "smoke test failed" }
        Write-Host "SMOKE OK"
        return
    }
    Write-Host "DevTeam AI running: UI http://127.0.0.1:5173  API http://127.0.0.1:8000  (Ctrl+C to stop)"
    Wait-Process -Id $api.Id
}
finally {
    foreach ($proc in @($web, $api)) {
        if ($null -ne $proc -and -not $proc.HasExited) {
            # /T stops the whole process tree (uv -> python, npm -> node).
            & taskkill.exe /PID $proc.Id /T /F | Out-Null
        }
    }
}
