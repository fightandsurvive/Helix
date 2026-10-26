# ══════════════════════════════════════════════════════════════════════
# Helix setup — Windows 11 (app-only build)
#
# Installs: Python 3.15, PostgreSQL 18, Node.js LTS, Rust, Ollama,
# Stockfish (latest), and initializes the database + virtual environment.
#
# Run from the repo root in an elevated PowerShell:
#   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
#   .\scripts\setup.ps1
# ══════════════════════════════════════════════════════════════════════

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path "$PSScriptRoot\..").Path
Set-Location $RepoRoot

Write-Host "[1/8] Installing system prerequisites via winget" -ForegroundColor Cyan

function Ensure-WingetPackage($id) {
    $installed = winget list --id $id 2>$null
    if (-not ($installed -match $id)) {
        Write-Host "  installing $id"
        winget install --id $id --silent --accept-package-agreements --accept-source-agreements
    } else {
        Write-Host "  $id already installed"
    }
}

Ensure-WingetPackage "Python.Python.3.15"
Ensure-WingetPackage "PostgreSQL.PostgreSQL.18"
Ensure-WingetPackage "OpenJS.NodeJS.LTS"
Ensure-WingetPackage "Ollama.Ollama"

# Visual Studio Build Tools required for Tauri (MSVC toolchain)
$vsWhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
if (-not (Test-Path $vsWhere)) {
    Write-Host "  installing Visual Studio Build Tools (required for Tauri)"
    winget install --id Microsoft.VisualStudio.2022.BuildTools --silent `
        --accept-package-agreements --accept-source-agreements `
        --override "--quiet --wait --add Microsoft.VisualStudio.Workload.VCTools"
}

# Rust toolchain
if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) {
    Write-Host "  installing rustup"
    $rustupInit = "$env:TEMP\rustup-init.exe"
    Invoke-WebRequest -Uri "https://win.rustup.rs/x86_64" -OutFile $rustupInit
    & $rustupInit -y --default-toolchain stable
    $env:Path = "$env:USERPROFILE\.cargo\bin;$env:Path"
}

Write-Host "[2/8] Configuring PostgreSQL" -ForegroundColor Cyan
# PostgreSQL installer puts binaries in Program Files
$pgBin = "C:\Program Files\PostgreSQL\18\bin"
if (-not (Test-Path "$pgBin\psql.exe")) {
    Write-Host "  ERROR: PostgreSQL bin not found at $pgBin" -ForegroundColor Red
    Write-Host "  Verify the install path and adjust `$pgBin in this script." -ForegroundColor Yellow
    exit 1
}
$env:Path = "$pgBin;$env:Path"

# Create the helix user and database (prompts for postgres password)
Write-Host "  Creating helix user and database..."
$pgSqlScript = @"
DO `$`$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_user WHERE usename = 'trin') THEN
        CREATE USER trin WITH PASSWORD 'trin_dev';
    END IF;
END
`$`$;

SELECT 'CREATE DATABASE helix_db OWNER trin'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'helix_db')\gexec
"@
$pgSqlScript | psql -U postgres -f -

# Enable required extensions (pg_trgm, btree_gin — pg_cron NOT supported on Windows)
Write-Host "  Enabling extensions on helix_db..."
"CREATE EXTENSION IF NOT EXISTS pg_trgm; CREATE EXTENSION IF NOT EXISTS btree_gin;" |
    psql -U postgres -d helix_db -f -

Write-Host "[3/8] Setting up Python virtual environment" -ForegroundColor Cyan
if (-not (Test-Path ".venv")) {
    py -3.15 -m venv .venv
}
& ".venv\Scripts\Activate.ps1"
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -e .

Write-Host "[4/8] Installing Stockfish" -ForegroundColor Cyan
$sfDir = "$env:USERPROFILE\bin"
New-Item -ItemType Directory -Force -Path $sfDir | Out-Null
if (-not (Test-Path "$sfDir\stockfish.exe")) {
    # Latest stable release — check github.com/official-stockfish/Stockfish/releases
    $sfUrl = "https://github.com/official-stockfish/Stockfish/releases/latest/download/stockfish-windows-x86-64-avx2.zip"
    $sfZip = "$env:TEMP\stockfish.zip"
    Invoke-WebRequest -Uri $sfUrl -OutFile $sfZip
    Expand-Archive -Path $sfZip -DestinationPath "$env:TEMP\sf-extract" -Force
    $sfExe = Get-ChildItem "$env:TEMP\sf-extract\stockfish" -Filter "*.exe" | Select-Object -First 1
    Copy-Item $sfExe.FullName "$sfDir\stockfish.exe"
    Write-Host "  Installed Stockfish to $sfDir\stockfish.exe"
} else {
    Write-Host "  Stockfish already at $sfDir\stockfish.exe"
}

Write-Host "[5/8] Pulling LLM model" -ForegroundColor Cyan
# 14B model fits A2000's 12GB VRAM at Q4_K_M
try {
    ollama pull deepseek-r1:14b
} catch {
    Write-Host "  (Ollama pull failed — LLM commentary will fall back to text)" -ForegroundColor Yellow
}

Write-Host "[6/8] Creating .env" -ForegroundColor Cyan
if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    # Update the stockfish path for Windows
    (Get-Content ".env") -replace "STOCKFISH_PATH=.*",
        "STOCKFISH_PATH=$($env:USERPROFILE -replace '\\','/')/bin/stockfish.exe" |
        Set-Content ".env"
    Write-Host "  Created .env — edit DATABASE_URL and OLLAMA_MODEL if needed"
}

Write-Host "[7/8] Running migrations" -ForegroundColor Cyan
$env:DATABASE_URL = "postgresql://trin:trin_dev@localhost:5432/helix_db"
python migrations/run_migrations.py

Write-Host "[8/8] Loading ECO data" -ForegroundColor Cyan
python -m ingest.eco_loader
python -m ingest.gambit_registry

Write-Host ""
Write-Host "✓ Setup complete." -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "  .venv\Scripts\Activate.ps1"
Write-Host "  python -m ingest.bulk_loader `$env:USERPROFILE\pgn\master --source lumbra_gb"
Write-Host "  python -m ingest.opening_lines"
Write-Host "  python -m ingest.puzzle_loader --source lichess"
Write-Host ""
Write-Host "  cd app; npm install; npm run tauri dev"
