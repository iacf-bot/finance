<#
.SYNOPSIS
  Stop IACF Finance (and the boot-time scheduled task, if it is running).
#>
[CmdletBinding()]
param([string]$TaskName = 'IACF Finance')
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'scripts\win_common.ps1')

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($task -and $task.State -eq 'Running') {
    try { Stop-ScheduledTask -TaskName $TaskName; Write-Host "Stopped scheduled task '$TaskName'." }
    catch { Write-Host "Could not stop task '$TaskName' ($($_.Exception.Message)); try an Administrator PowerShell." -ForegroundColor Yellow }
    Start-Sleep -Seconds 1
}
$stopped = $false
for ($i = 0; $i -lt 5; $i++) {
    $p = Get-ServerProcess
    if (-not $p) { break }
    # SQLite in WAL mode is crash-safe; a hard stop cannot corrupt the database.
    Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
    $stopped = $true
    Start-Sleep -Milliseconds 500
}
Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path (Get-DataDir) 'server.pid')
if ($stopped) { Write-Host 'Stopped' -ForegroundColor Green } else { Write-Host 'Not running' }
