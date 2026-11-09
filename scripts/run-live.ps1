#Requires -Version 5.1
<#
  Vaelen live launcher (Windows).
  - Builds release, validates live config, checks creds, runs engine.
  - Paper mode is default; pass -Live to trade real orders.
#>
param(
  [switch]$Live,
  [string]$Config = "config.live.toml"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $Config)) { throw "missing $Config" }

Write-Host "== Vaelen pre-flight =="
cargo build --release
if ($LASTEXITCODE -ne 0) { throw "build failed" }

if ($Live) {
  if ([string]::IsNullOrWhiteSpace($env:DELTA_API_KEY) -or [string]::IsNullOrWhiteSpace($env:DELTA_API_SECRET)) {
    throw "Live requires DELTA_API_KEY and DELTA_API_SECRET env vars"
  }
  Write-Host "Verifying credentials..."
  cargo run --release --bin verify
  if ($LASTEXITCODE -ne 0) { throw "verify failed" }
  $env:DELTA__GENERAL__PAPER_TRADE_MODE = "false"
} else {
  $env:DELTA__GENERAL__PAPER_TRADE_MODE = "true"
  Write-Host "PAPER mode (no real orders)."
}

Write-Host "Starting engine with $Config ..."
cargo run --release -- $Config
