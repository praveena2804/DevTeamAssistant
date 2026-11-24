<#
.SYNOPSIS
  Start the DevTeam AI API, the run worker and the web UI for local development (bound to 127.0.0.1).

.DESCRIPTION
  API:  http://127.0.0.1:8000  (uv run python -m devteam_api)
  UI:   http://127.0.0.1:5173  (Vite dev server; proxies /health, /version and /api to the API)
  Sandbox: keeps the WSL distribution with the Docker Engine open while the platform runs (Part E, D-07 (b));
  WSL stops an idle distribution, and the engine with it. If WSL or the engine is missing, sandbox work is
  Blocked; nothing else changes.
  Worker: uv run python -m devteam_worker (D-41: runs execute here, never in the API). It starts after the
  API has migrated the database, uses the local Ollama only, and refuses to start if a second worker runs.
  Press Ctrl+C to stop all processes.

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
$worker = $null
$wslKeepAlive = $null

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

# D-18a: a Part B plaintext key file is moved into Windows Credential Manager first (four checks; the file
# is deleted only if all pass). The API refuses to start while the file exists.
if (Test-Path (Join-Path $root "data/api_token")) {
    Write-Host "Moving the sign-in key into Windows Credential Manager (one-time)..."
    & uv run python (Join-Path $root "scripts/migrate_api_token.py")
    if ($LASTEXITCODE -ne 0) { throw "Key migration stopped; the original key file was kept. See the messages above." }
}

try {
    # Part E: keep the WSL distribution (and its Docker Engine) running; WSL stops an idle distribution.
    $distro = (& uv run python -c "from devteam_core.config import load_settings; print(load_settings().sandbox.wsl_distribution)").Trim()
    try {
        $wslKeepAlive = Start-Process -FilePath "wsl.exe" -ArgumentList @("-d", $distro, "--exec", "sleep", "infinity") `
            -WindowStyle Hidden -PassThru
        Write-Host "Sandbox: WSL distribution '$distro' kept open (Docker Engine)"
    }
    catch {
        Write-Host "Sandbox: WSL distribution '$distro' not available; sandbox work will be Blocked"
    }

    $api = Start-Process -FilePath "uv" -ArgumentList @("run", "python", "-m", "devteam_api") `
        -WorkingDirectory $root -PassThru -NoNewWindow
    $web = Start-Process -FilePath "npm.cmd" -ArgumentList @("--prefix", "apps/web", "run", "dev") `
        -WorkingDirectory $root -PassThru -NoNewWindow

    $direct = Wait-Http "http://127.0.0.1:8000/health"
    Write-Host "API health: $($direct.status)"
    $proxied = Wait-Http "http://127.0.0.1:5173/health"
    Write-Host "UI proxy health: $($proxied.status)"

    # The worker starts once the API has migrated the state database (it refuses an unmigrated one).
    $worker = Start-Process -FilePath "uv" -ArgumentList @("run", "python", "-m", "devteam_worker") `
        -WorkingDirectory $root -PassThru -NoNewWindow
    Start-Sleep -Seconds 5
    if ($worker.HasExited) { throw "the run worker stopped at start (exit $($worker.ExitCode)); see the messages above" }
    Write-Host "Run worker: started (process $($worker.Id))"

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
    Write-Host "DevTeam AI running: UI http://127.0.0.1:5173  API http://127.0.0.1:8000  worker  (Ctrl+C to stop)"
    Write-Host "Sign in with this link (keep it private): $link"
    if (-not $NoBrowser) { Start-Process $link }
    Wait-Process -Id $api.Id
}
finally {
    foreach ($proc in @($worker, $web, $api, $wslKeepAlive)) {
        if ($null -ne $proc -and -not $proc.HasExited) {
            # /T stops the whole process tree (uv -> python, npm -> node). A killed worker is safe:
            # the next one recovers the run from its last checkpoint (D-40).
            & taskkill.exe /PID $proc.Id /T /F | Out-Null
        }
    }
}
