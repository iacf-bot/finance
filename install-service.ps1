<#
.SYNOPSIS
  OPTIONAL: start IACF Finance automatically when Windows boots (Windows Scheduled Task). Run in an Administrator PowerShell.
.EXAMPLE
  .\install-service.ps1               # task runs as you, whether or not you are logged in (no password stored)
  .\install-service.ps1 -AsSystem     # run as the SYSTEM account instead
  .\install-service.ps1 -StartNow     # also start it right away
  .\install-service.ps1 -Uninstall    # remove the task
.NOTES
  Alternative: NSSM (https://nssm.cc) - see RUN_ON_WINDOWS.md.
#>
[CmdletBinding()]
param([switch]$Uninstall, [switch]$AsSystem, [switch]$StartNow, [string]$TaskName = 'IACF Finance')
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'scripts\win_common.ps1')

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { Write-Host 'Please run this in an Administrator PowerShell (right-click > Run as administrator).' -ForegroundColor Red; exit 1 }

if ($Uninstall) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Removed scheduled task '$TaskName'. The app itself and its data are untouched." -ForegroundColor Green
    } else { Write-Host "No task named '$TaskName'." }
    exit 0
}

if (-not (Test-Path (Get-VenvPython))) { Write-Host 'Run setup.ps1 first.' -ForegroundColor Red; exit 1 }
$startPs1 = Join-Path $AppDir 'start.ps1'
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -WorkingDirectory $AppDir `
    -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$startPs1`" -Service"
$trigger = New-ScheduledTaskTrigger -AtStartup
$trigger.Delay = 'PT30S'   # give the network / Tailscale a moment
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
if ($AsSystem) {
    $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
    $who = 'SYSTEM'
} else {
    $user = "$env:USERDOMAIN\$env:USERNAME"
    # S4U = runs whether or not you are logged on, without storing your password
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType S4U -RunLevel Limited
    $who = $user
}
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
    -Description "IACF Finance server ($AppDir). Logs: $(Join-Path (Get-DataDir) 'logs\server.log')" -Force | Out-Null
Write-Host "Registered scheduled task '$TaskName' (runs at boot as $who, restarts every minute if it stops with an error)." -ForegroundColor Green
if ($StartNow) {
    if (Get-ServerProcess) { Write-Host 'The app is already running (started by start.ps1); the task will take over after the next reboot.' }
    else { Start-ScheduledTask -TaskName $TaskName; Start-Sleep -Seconds 5; if (Test-Healthz 5) { Write-Host "Running: $(Get-LocalUrl)" -ForegroundColor Green } else { Write-Host 'Started; check the log if the page does not open.' } }
}
Write-Host 'Manage it in Task Scheduler (taskschd.msc) or remove it with: .\install-service.ps1 -Uninstall'
