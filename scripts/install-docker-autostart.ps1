<#
.SYNOPSIS
  OPTIONAL (Docker setup): make the iacf-finance container reachable over Tailscale and keep it that way after reboots.
  Run in an Administrator PowerShell.
  - Adds a Windows Firewall rule allowing TCP 8088 inbound on the Tailscale interface only (from 100.64.0.0/10).
  - Registers a logon task that runs scripts\docker-up-tailscale.ps1 (waits for Docker + Tailscale, then compose up).
.EXAMPLE
  .\scripts\install-docker-autostart.ps1
  .\scripts\install-docker-autostart.ps1 -Uninstall
#>
[CmdletBinding()]
param([switch]$Uninstall, [int]$Port = 8088, [string]$TaskName = 'IACF Finance (Docker)')
$ErrorActionPreference = 'Stop'
$ruleName = "IACF Finance $Port (Tailscale)"

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { Write-Host 'Please run this in an Administrator PowerShell (right-click > Run as administrator).' -ForegroundColor Red; exit 1 }

if ($Uninstall) {
    Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) { Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false }
    Write-Host "Removed firewall rule '$ruleName' and task '$TaskName'." -ForegroundColor Green
    exit 0
}

Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Protocol TCP -LocalPort $Port `
    -InterfaceAlias Tailscale -RemoteAddress 100.64.0.0/10 -Action Allow -Profile Any | Out-Null
Write-Host "Firewall rule '$ruleName' added." -ForegroundColor Green

# Docker Desktop runs per-user at logon, so the task runs as the invoking user at their logon.
$user = "$env:USERDOMAIN\$env:USERNAME"
$script = Join-Path $PSScriptRoot 'docker-up-tailscale.ps1'
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -WorkingDirectory (Split-Path $PSScriptRoot -Parent) `
    -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$trigger.Delay = 'PT20S'
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 15) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
Write-Host "Logon task '$TaskName' registered for $user. Log: data\docker-autostart.log" -ForegroundColor Green
