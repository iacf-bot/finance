<#
.SYNOPSIS
  One-time setup for IACF Finance on Windows: checks Python 3.11+, creates .venv and installs requirements.txt.
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\setup.ps1
  .\setup.ps1 -Recreate     # delete and rebuild .venv (e.g. after upgrading Python)
#>
[CmdletBinding()]
param([switch]$Recreate)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'scripts\win_common.ps1')
Set-Location -LiteralPath $AppDir

function Find-Python {
    # Prefer the py launcher (python.org installer), then python/python3 on PATH.
    # The Microsoft Store "python.exe" alias prints nothing and fails - it is skipped automatically.
    $candidates = @(@('py', '-3.13'), @('py', '-3.12'), @('py', '-3.11'), @('py', '-3'), @('python'), @('python3'))
    foreach ($c in $candidates) {
        if (-not (Get-Command $c[0] -ErrorAction SilentlyContinue)) { continue }
        $pre = @(); if ($c.Count -gt 1) { $pre = $c[1..($c.Count - 1)] }
        try {
            $out = & $c[0] @pre -c "import sys; print('%d.%d' % sys.version_info[:2]); print(sys.executable)" 2>$null
        } catch { continue }
        if ($LASTEXITCODE -ne 0 -or -not $out -or @($out).Count -lt 2) { continue }
        $ver = [version]@($out)[0]
        if ($ver -ge [version]'3.11') { return @{ Exe = @($out)[1]; Version = $ver } }
        Write-Host "  found Python $ver at $(@($out)[1]) - too old (need 3.11+)" -ForegroundColor Yellow
    }
    return $null
}

Write-Host '== IACF Finance setup ==' -ForegroundColor Cyan
$py = Find-Python
if (-not $py) {
    Write-Host @'
Python 3.11 or newer was not found.
  1. Download it from https://www.python.org/downloads/windows/ (Windows installer, 64-bit).
  2. In the installer tick "Add python.exe to PATH" (and keep "py launcher" selected).
  3. Close and reopen PowerShell, then run setup again.
If "python" opens the Microsoft Store, turn off the alias in
Settings > Apps > Advanced app settings > App execution aliases (python.exe / python3.exe).
'@ -ForegroundColor Red
    exit 1
}
Write-Host "Python $($py.Version): $($py.Exe)"

$venvPy = Get-VenvPython
if ((Test-Path $venvPy) -and -not $Recreate) {
    $vv = & $venvPy -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $vv -or [version]$vv -lt [version]'3.11') {
        Write-Host 'Existing .venv is broken or too old; rebuilding it.' -ForegroundColor Yellow
        $Recreate = $true
    }
}
if ($Recreate -and (Test-Path (Join-Path $AppDir '.venv'))) {
    if (Get-ServerProcess) { Write-Host 'Stop the server first (stop.ps1).' -ForegroundColor Red; exit 1 }
    Remove-Item -Recurse -Force (Join-Path $AppDir '.venv')
}
if (-not (Test-Path $venvPy)) {
    Write-Host 'Creating virtual environment .venv ...'
    & $py.Exe -m venv (Join-Path $AppDir '.venv')
    if ($LASTEXITCODE -ne 0) { Write-Host 'Could not create .venv' -ForegroundColor Red; exit 1 }
}
Write-Host 'Installing packages (a few minutes the first time) ...'
& $venvPy -m pip install --disable-pip-version-check -q --upgrade pip
& $venvPy -m pip install --disable-pip-version-check -r (Join-Path $AppDir 'requirements.txt')
if ($LASTEXITCODE -ne 0) {
    Write-Host 'pip install failed (see the messages above). Check the internet connection / proxy and run setup again.' -ForegroundColor Red
    exit 1
}
& $venvPy -c "import fastapi, uvicorn, cryptography, zoneinfo; zoneinfo.ZoneInfo('America/Los_Angeles'); print('Packages OK')"
if ($LASTEXITCODE -ne 0) { exit 1 }

# Optional: Tesseract OCR for receipt photos (runs locally; without it photos are saved but not read)
$tess = Get-Command tesseract -ErrorAction SilentlyContinue
if (-not $tess -and -not (Test-Path 'C:\Program Files\Tesseract-OCR\tesseract.exe')) {
    Write-Host 'Optional: receipt-photo OCR needs Tesseract. Install it with one of:' -ForegroundColor Yellow
    Write-Host '    winget install UB-Mannheim.TesseractOCR      (or: choco install tesseract)' -ForegroundColor Yellow
    Write-Host '  For Chinese receipts also add chi_sim.traineddata to C:\Program Files\Tesseract-OCR\tessdata' -ForegroundColor Yellow
    Write-Host '  (https://github.com/tesseract-ocr/tessdata). Then restart the app.' -ForegroundColor Yellow
} else { Write-Host 'Tesseract OCR found (receipt photos will be read locally).' }

$data = Get-DataDir
New-Item -ItemType Directory -Force -Path (Join-Path $data 'logs') | Out-Null
# Windows equivalent of "chmod 700 data": only you, SYSTEM and Administrators may open the data folder
# (finance.db, backups, secret.key, API_KEY.txt). Skipped quietly if not possible (e.g. some network drives).
try {
    $me = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    & icacls "$data" /inheritance:r /grant:r "${me}:(OI)(CI)F" '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' /Q | Out-Null
    if ($LASTEXITCODE -eq 0) { Write-Host "Data folder permissions limited to $me, SYSTEM and Administrators." }
} catch { Write-Host "Note: could not tighten permissions on $data ($($_.Exception.Message))." -ForegroundColor Yellow }
$envFile = Join-Path $AppDir '.env'
if (-not (Test-Path $envFile) -and (Test-Path (Join-Path $AppDir '.env.example'))) {
    Copy-Item (Join-Path $AppDir '.env.example') $envFile
    Write-Host 'Created .env from .env.example (all settings commented out = defaults).'
}
Write-Host ''
Write-Host "Setup complete. Data folder: $data" -ForegroundColor Green
Write-Host 'Next: double-click start.bat (or run .\start.ps1), then open' (Get-LocalUrl)
