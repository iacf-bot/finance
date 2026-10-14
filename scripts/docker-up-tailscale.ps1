<#
.SYNOPSIS
  Wait until Docker Desktop and the Tailscale IP are both ready, then `docker compose up -d`.
  Docker's own restart policy fails if it starts the container before Tailscale has its IP
  (the "100.x.x.x:8088" port binding can't be created), so this runs at logon to fix that.
  Installed as a logon task by scripts\install-docker-autostart.ps1.
#>
[CmdletBinding()]
param([int]$TimeoutMinutes = 10)
$ErrorActionPreference = 'Continue'
$AppDir = Split-Path $PSScriptRoot -Parent
$log = Join-Path $AppDir 'data\docker-autostart.log'
function Log($m) { "$(Get-Date -Format s)  $m" | Add-Content -Path $log -Encoding utf8 }

# The Tailscale IP is whichever 100.x.x.x address the compose file binds to (docker-compose.local.yml if present).
$tsIp = (Select-String -Path (Join-Path $AppDir $(if (Test-Path (Join-Path $AppDir 'docker-compose.local.yml')) { 'docker-compose.local.yml' } else { 'docker-compose.yml' })) -Pattern '"(100\.\d+\.\d+\.\d+):\d+:\d+"').Matches |
    Select-Object -First 1 | ForEach-Object { $_.Groups[1].Value }
$deadline = (Get-Date).AddMinutes($TimeoutMinutes)
Log "start (tailscale ip in compose: $tsIp)"

while ((Get-Date) -lt $deadline) {
    $dockerOk = $false
    docker info *> $null; if ($LASTEXITCODE -eq 0) { $dockerOk = $true }
    $tsOk = (-not $tsIp) -or [bool](Get-NetIPAddress -IPAddress $tsIp -ErrorAction SilentlyContinue)
    if ($dockerOk -and $tsOk) { break }
    Start-Sleep -Seconds 5
}
if ((Get-Date) -ge $deadline) { Log "timed out (docker=$dockerOk tailscale=$tsOk)"; exit 1 }

Push-Location $AppDir
try {
    $out = cmd /c "docker compose up -d 2>&1" | Out-String
    Log "compose up -d (exit $LASTEXITCODE): $($out.Trim())"
    # If the container was started by Docker before Tailscale was up, it may be running
    # without the Tailscale binding (or exited). Recreate it so all ports bind.
    $ports = docker ps --filter name=iacf-finance --format '{{.Ports}}'
    if ($tsIp -and ($ports -notmatch [regex]::Escape($tsIp))) {
        $out = cmd /c "docker compose up -d --force-recreate 2>&1" | Out-String
        Log "recreated (exit $LASTEXITCODE): $($out.Trim())"
    }
    Log "ports: $(docker ps --filter name=iacf-finance --format '{{.Ports}}')"
} finally { Pop-Location }
