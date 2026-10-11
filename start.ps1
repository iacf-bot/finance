<#
.SYNOPSIS
  Start IACF Finance.
.DESCRIPTION
  Default: starts the server in the background (hidden window); logs go to <DATA_DIR>\logs\server.log.
  -Foreground : run in this window (logs on screen, Ctrl+C stops it).
  -Service    : used by the scheduled task (install-service.ps1): runs in the foreground, logs to the file,
                waits for the configured addresses (e.g. Tailscale) and exits with an error code so the task restarts it.
  Settings (HOST, PORT, DATA_DIR) come from .env or environment variables.
#>
[CmdletBinding()]
param([switch]$Foreground, [switch]$Service)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'scripts\win_common.ps1')
Set-Location -LiteralPath $AppDir

$py = Get-VenvPython
if (-not (Test-Path $py)) {
    Write-Host 'First run: running setup.ps1 ...' -ForegroundColor Cyan
    & (Join-Path $AppDir 'setup.ps1')
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $py)) { exit 1 }
}

$data = Get-DataDir
$logs = Join-Path $data 'logs'
New-Item -ItemType Directory -Force -Path $logs | Out-Null
$log = Join-Path $logs 'server.log'
$pidFile = Join-Path $data 'server.pid'
$port = Get-Port
$hosts = Get-Hosts

$running = Get-ServerProcess
if ($running) {
    Write-Host "Already running (pid $($running.Id)). Open $(Get-LocalUrl)"
    exit 0
}

$busy = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($busy) {
    $owner = Get-Process -Id $busy.OwningProcess -ErrorAction SilentlyContinue
    Write-Host "Port $port is already in use by $($owner.ProcessName) (pid $($busy.OwningProcess))." -ForegroundColor Red
    Write-Host 'Stop that program, or set another port in .env (PORT=8089) and start again.'
    exit 1
}

if ($Service) {
    # At boot Tailscale may come up after us: wait (max 3 min) for every configured address to exist.
    $want = @($hosts | Where-Object { $_ -ne '127.0.0.1' -and $_ -ne '0.0.0.0' -and $_ -ne 'localhost' })
    for ($i = 0; $i -lt 90 -and $want.Count -gt 0; $i++) {
        $have = @(Get-NetIPAddress -ErrorAction SilentlyContinue | ForEach-Object { $_.IPAddress })
        $missing = @($want | Where-Object { $have -notcontains $_ })
        if ($missing.Count -eq 0) { break }
        Start-Sleep -Seconds 2
    }
    $env:FINANCE_REQUIRE_ALL_HOSTS = '1'
}

$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUNBUFFERED = '1'
$env:FINANCE_PID_FILE = $pidFile

if ($Foreground) {
    Write-Host "IACF Finance on $(Get-LocalUrl)  (Ctrl+C to stop; data: $data)" -ForegroundColor Green
    & $py -m app.serve
    exit $LASTEXITCODE
}

if ((Test-Path $log) -and (Get-Item $log).Length -gt 10MB) { Move-Item -Force $log ($log + '.1') }
$cmdLine = '/d /c ""' + $py + '" -m app.serve >> "' + $log + '" 2>&1"'
if ($Service) {
    # stay attached so Task Scheduler sees the process and its exit code
    $p = Start-Process -FilePath $env:ComSpec -ArgumentList $cmdLine -WorkingDirectory $AppDir -WindowStyle Hidden -PassThru -Wait
    exit $p.ExitCode
}

Start-Process -FilePath $env:ComSpec -ArgumentList $cmdLine -WorkingDirectory $AppDir -WindowStyle Hidden | Out-Null
for ($i = 0; $i -lt 40; $i++) {
    Start-Sleep -Milliseconds 500
    if (Test-Healthz 2) { break }
}
$proc = Get-ServerProcess
if ($proc -and (Test-Healthz 3)) {
    Write-Host "Started (pid $($proc.Id)). Open $(Get-LocalUrl)" -ForegroundColor Green
    foreach ($h in $hosts) { if ($h -ne '127.0.0.1') { Write-Host "  also listening on http://${h}:$port" } }
    Write-Host "Log: $log"
    Get-Content -LiteralPath $log -Tail 40 -ErrorAction SilentlyContinue | Select-String -Pattern 'Listening on|Could not bind|ALL interfaces' | Select-Object -Last 4 | ForEach-Object { Write-Host "  $($_.Line)" }
    exit 0
}
Write-Host "Failed to start. Last lines of $log :" -ForegroundColor Red
Get-Content -LiteralPath $log -Tail 25 -ErrorAction SilentlyContinue
exit 1
