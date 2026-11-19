<#
.SYNOPSIS
  Start the DevTeam AI API and web UI for local development (both bound to 127.0.0.1).

.DESCRIPTION
  API:  http://127.0.0.1:8000  (uv run python -m devteam_api)
  UI:   http://127.0.0.1:5173  (Vite dev server; proxies /health, /version and /api to the API)
  Press Ctrl+C to stop both processes.

.PARAMETER Smoke
  Start both processes, verify /health directly and through the UI proxy, verify that /api refuses requests
  without the local key and accepts them with it, then stop (used as a smoke test).

.PARAMETER NoBrowser
  Do not open the browser; only print the sign-in link.

.NOTES
  The sign-in link contains the local key after '#'. Browsers never send that part to any server.
  Do not share the link: it lets anyone on this computer use DevTeam AI as you.
#>
[CmdletBinding()]
param(
    [switch]$Smoke,
    [switch]$NoBrowser
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

    $link = (& uv run python -m devteam_api.signin_link).Trim()

    if ($Smoke) {
        if ($direct.status -ne "ok" -or $proxied.status -ne "ok") { throw "smoke test failed: health" }
        $anonymous = $null
        try {
            Invoke-RestMethod -Uri "http://127.0.0.1:5173/api/projects" -TimeoutSec 10 | Out-Null
        }
        catch {
            $anonymous = [int]$_.Exception.Response.StatusCode
        }
        if ($anonymous -ne 401) { throw "smoke test failed: /api without the key answered $anonymous, expected 401" }
        $token = $link.Substring($link.IndexOf("#token=") + 7)
        $projects = Invoke-RestMethod -Uri "http://127.0.0.1:5173/api/projects" -TimeoutSec 10 `
            -Headers @{ Authorization = "Bearer $token" }
        Write-Host "API auth: refused without key (401), accepted with key ($(@($projects).Count) projects)"
        Write-Host "SMOKE OK"
        return
    }
    Write-Host "DevTeam AI running: UI http://127.0.0.1:5173  API http://127.0.0.1:8000  (Ctrl+C to stop)"
    Write-Host "Sign in with this link (keep it private): $link"
    if (-not $NoBrowser) { Start-Process $link }
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
