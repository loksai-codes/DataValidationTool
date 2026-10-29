# ==============================================================================
# DATA MIGRATION VALIDATOR - LOCAL STARTUP SCRIPT (WINDOWS POWERSHELL)
# ==============================================================================

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "DATA MIGRATION VALIDATOR - LOCAL STARTUP" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

# 1. Verify Python
$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCmd) {
    Write-Host "[ERROR] Python 3.12+ was not found in PATH." -ForegroundColor Red
    exit 1
}

$pythonVersion = python --version
Write-Host "[1/4] Found Python: $pythonVersion" -ForegroundColor Green

# 2. Run Environment Verification
Write-Host "[2/4] Verifying Environment, Connectors & Profiles..." -ForegroundColor Yellow
python scripts/check_environment.py
if ($LASTEXITCODE -ne 0) {
    Write-Host "[WARN] Environment check reported notices. Continuing startup..." -ForegroundColor Yellow
}

# 3. Ensure Metadata Schema Initialized
Write-Host "[3/4] Ensuring 'validator_meta' Schema Is Initialized..." -ForegroundColor Yellow
python scripts/initialize_metadata.py
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] Failed to initialize metadata repository." -ForegroundColor Red
    exit 1
}

# 4. Launch Streamlit Application
Write-Host "[4/4] Launching Mission Control UI on http://localhost:8501..." -ForegroundColor Green
Write-Host "Press Ctrl+C to stop the application." -ForegroundColor Gray
streamlit run app.py
