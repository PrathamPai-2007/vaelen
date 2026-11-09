#Requires -Version 5.1
#Requires -RunAsAdministrator
<#
  Install Vaelen as a Windows service via NSSM (https://nssm.cc).
  Usage (admin PowerShell):
    .\scripts\install-service.ps1 -NssmPath C:\nssm\nssm.exe
#>
param(
  [string]$NssmPath = "C:\nssm\nssm.exe",
  [string]$AppDir = (Get-Location).Path,
  [string]$ServiceName = "VaelenLive"
)

$ErrorActionPreference = "Stop"
if (-not (Test-Path $NssmPath)) { throw "NSSM not found at $NssmPath" }

$exe = Join-Path $AppDir "target\release\delta_trading_engine.exe"
if (-not (Test-Path $exe)) { throw "missing $exe; run cargo build --release first" }

& $NssmPath install $ServiceName $exe "config.live.toml"
& $NssmPath set $ServiceName AppDirectory $AppDir
& $NssmPath set $ServiceName AppStdout (Join-Path $AppDir "logs\service.out.log")
& $NssmPath set $ServiceName AppStderr (Join-Path $AppDir "logs\service.err.log")
& $NssmPath set $ServiceName Start SERVICE_AUTO_START
& $NssmPath set $ServiceName AppStopMethodConsole 30000
& $NssmPath set $ServiceName AppThrottle 5000
Write-Host "Installed $ServiceName. Set DELTA_API_KEY/DELTA_API_SECRET as service env, then: nssm start $ServiceName"
