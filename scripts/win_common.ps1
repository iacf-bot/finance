# Shared helpers for the IACF Finance Windows scripts (dot-sourced by setup.ps1 / start.ps1 / stop.ps1 / install-service.ps1).
# Compatible with Windows PowerShell 5.1 and PowerShell 7+.

$AppDir = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path (Join-Path $AppDir 'app\serve.py'))) { $AppDir = $PSScriptRoot }

function Get-DotEnv {
    # Reads KEY=VALUE pairs from .env (same rules as app/config.py). Returns a hashtable.
    $h = @{}
    $f = Join-Path $AppDir '.env'
    if (Test-Path -LiteralPath $f) {
        foreach ($raw in Get-Content -LiteralPath $f -Encoding UTF8) {
            $line = $raw.Trim()
            if ($line -eq '' -or $line.StartsWith('#') -or -not $line.Contains('=')) { continue }
            if ($line.StartsWith('export ')) { $line = $line.Substring(7).TrimStart() }
            $i = $line.IndexOf('=')
            $k = $line.Substring(0, $i).Trim().TrimStart([char]0xFEFF)
            $v = $line.Substring($i + 1).Trim()
            if ($v.Length -ge 2 -and ($v[0] -eq '"' -or $v[0] -eq "'") -and $v[-1] -eq $v[0]) { $v = $v.Substring(1, $v.Length - 2) }
            elseif ($v -match '\s#') { $v = ($v -split '\s#', 2)[0].TrimEnd() }
            $h[$k] = $v
        }
    }
    return $h
}

function Get-Setting([string[]]$Names, [string]$Default) {
    # Environment variables win over .env (like the app itself).
    $envFile = Get-DotEnv
    foreach ($n in $Names) {
        $v = [Environment]::GetEnvironmentVariable($n)
        if ($v -and $v.Trim()) { return $v.Trim() }
    }
    foreach ($n in $Names) {
        if ($envFile.ContainsKey($n) -and $envFile[$n].Trim()) { return $envFile[$n].Trim() }
    }
    return $Default
}

function Get-DataDir {
    $d = Get-Setting @('DATA_DIR', 'FINANCE_DATA_DIR') 'data'
    $d = [Environment]::ExpandEnvironmentVariables($d)
    if (-not [System.IO.Path]::IsPathRooted($d)) { $d = Join-Path $AppDir $d }
    return [System.IO.Path]::GetFullPath($d)
}

function Get-Port { return [int](Get-Setting @('FINANCE_PORT', 'PORT') '8088') }

function Get-Hosts {
    $h = Get-Setting @('FINANCE_HOSTS', 'HOST') '127.0.0.1'
    return @($h -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
}

function Get-VenvPython { return (Join-Path $AppDir '.venv\Scripts\python.exe') }

function Get-ServerProcess {
    # The running server: first the pid the server wrote itself, then any python started from this app's .venv.
    $pidFile = Join-Path (Get-DataDir) 'server.pid'
    if (Test-Path -LiteralPath $pidFile) {
        $id = (Get-Content -LiteralPath $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
        if ($id -match '^\d+$') {
            $p = Get-Process -Id ([int]$id) -ErrorAction SilentlyContinue
            if ($p -and $p.ProcessName -like 'python*') { return $p }
        }
    }
    $venv = (Join-Path $AppDir '.venv').ToLower()
    $procs = @(Get-CimInstance Win32_Process -Filter "Name like 'python%'" -ErrorAction SilentlyContinue)
    $launchers = @($procs | Where-Object { $_.CommandLine -and $_.CommandLine -like '*app.serve*' -and $_.ExecutablePath -and $_.ExecutablePath.ToLower().StartsWith($venv) })
    foreach ($l in $launchers) {
        # A venv python.exe on Windows is a small launcher that runs the real interpreter as a child process.
        $child = $procs | Where-Object { $_.ParentProcessId -eq $l.ProcessId } | Select-Object -First 1
        if ($child) { return (Get-Process -Id $child.ProcessId -ErrorAction SilentlyContinue) }
        return (Get-Process -Id $l.ProcessId -ErrorAction SilentlyContinue)
    }
    return $null
}

function Get-LocalUrl {
    $port = Get-Port
    $hosts = Get-Hosts
    if ($hosts -contains '127.0.0.1' -or $hosts -contains '0.0.0.0' -or $hosts.Count -eq 0) { return "http://127.0.0.1:$port" }
    return "http://$($hosts[0]):$port"
}

function Test-Healthz([int]$TimeoutSec = 3) {
    try {
        $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec $TimeoutSec -Uri ((Get-LocalUrl) + '/healthz')
        return ($r.StatusCode -eq 200)
    } catch { return $false }
}
