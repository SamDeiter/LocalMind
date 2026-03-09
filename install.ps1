# ============================================================================
# LocalMind — Installation Script (PowerShell)
#
# One-command setup: installs Ollama, pulls models, creates Python venv,
# installs dependencies, creates workspace, and sets up a launch shortcut.
#
# Usage: Right-click > Run with PowerShell, or:
#   powershell -ExecutionPolicy Bypass -File install.ps1
# ============================================================================

$ErrorActionPreference = "Stop"

# ── Colors for pretty output ────────────────────────────────────────
function Write-Step { param([string]$msg) Write-Host "`n🔧 $msg" -ForegroundColor Cyan }
function Write-OK { param([string]$msg) Write-Host "   ✅ $msg" -ForegroundColor Green }
function Write-Warn { param([string]$msg) Write-Host "   ⚠️  $msg" -ForegroundColor Yellow }
function Write-Err { param([string]$msg) Write-Host "   ❌ $msg" -ForegroundColor Red }

$ProjectRoot = $PSScriptRoot
$Workspace = Join-Path $HOME "LocalMind_Workspace"
$VenvPath = Join-Path $ProjectRoot "venv"

Write-Host ""
Write-Host "════════════════════════════════════════════" -ForegroundColor Magenta
Write-Host "   🧠 LocalMind — Installation" -ForegroundColor White
Write-Host "════════════════════════════════════════════" -ForegroundColor Magenta

# ── Step 1: Check for Ollama ────────────────────────────────────────
Write-Step "Checking for Ollama..."

$ollamaPath = Get-Command ollama -ErrorAction SilentlyContinue
if ($ollamaPath) {
    $version = & ollama --version 2>&1
    Write-OK "Ollama found: $version"
}
else {
    Write-Warn "Ollama not found. Installing..."
    Write-Host "   Downloading Ollama installer..." -ForegroundColor Gray

    $installerUrl = "https://ollama.com/download/OllamaSetup.exe"
    $installerPath = Join-Path $env:TEMP "OllamaSetup.exe"

    try {
        Invoke-WebRequest -Uri $installerUrl -OutFile $installerPath -UseBasicParsing
        Write-Host "   Running installer (follow the prompts)..." -ForegroundColor Gray
        Start-Process -FilePath $installerPath -Wait
        Write-OK "Ollama installed. You may need to restart your terminal."
    }
    catch {
        Write-Err "Failed to download Ollama. Please install manually from https://ollama.com"
        Write-Host "   After installing, re-run this script." -ForegroundColor Gray
        exit 1
    }
}

# ── Step 2: Start Ollama if not running ─────────────────────────────
Write-Step "Ensuring Ollama is running..."

$ollamaRunning = $null
try {
    $ollamaRunning = Invoke-RestMethod -Uri "http://localhost:11434/api/tags" -Method Get -TimeoutSec 3 -ErrorAction SilentlyContinue
}
catch {}

if (-not $ollamaRunning) {
    Write-Host "   Starting Ollama..." -ForegroundColor Gray
    Start-Process ollama -ArgumentList "serve" -WindowStyle Hidden
    Start-Sleep -Seconds 3
    Write-OK "Ollama started"
}
else {
    Write-OK "Ollama is already running"
}

# ── Step 3: Pull models ────────────────────────────────────────────
$models = @(
    @{ Name = "qwen2.5-coder:32b"; Desc = "Main chat/coding model (~20GB)" },
    @{ Name = "llama3.2-vision:11b"; Desc = "Vision/image analysis model (~7GB)" },
    @{ Name = "nomic-embed-text"; Desc = "Memory embeddings (~300MB)" }
)

Write-Step "Pulling AI models (this may take a while on first run)..."

foreach ($model in $models) {
    Write-Host "   Pulling $($model.Name) — $($model.Desc)" -ForegroundColor Gray

    try {
        & ollama pull $model.Name
        Write-OK "$($model.Name) ready"
    }
    catch {
        Write-Warn "Failed to pull $($model.Name). You can pull it later with: ollama pull $($model.Name)"
    }
}

# ── Step 4: Python virtual environment ──────────────────────────────
Write-Step "Setting up Python environment..."

if (-not (Test-Path $VenvPath)) {
    python -m venv $VenvPath
    Write-OK "Virtual environment created"
}
else {
    Write-OK "Virtual environment already exists"
}

# Activate and install deps
$pipPath = Join-Path $VenvPath "Scripts" "pip.exe"
$reqPath = Join-Path $ProjectRoot "requirements.txt"

& $pipPath install -r $reqPath --quiet
Write-OK "Python dependencies installed"

# ── Step 5: Create workspace directory ──────────────────────────────
Write-Step "Creating workspace..."

if (-not (Test-Path $Workspace)) {
    New-Item -ItemType Directory -Path $Workspace | Out-Null
    Write-OK "Workspace created at: $Workspace"
}
else {
    Write-OK "Workspace already exists: $Workspace"
}

# ── Step 6: Create desktop shortcut ─────────────────────────────────
Write-Step "Creating desktop shortcut..."

$desktopPath = [Environment]::GetFolderPath("Desktop")
$shortcutPath = Join-Path $desktopPath "LocalMind.lnk"
$startScript = Join-Path $ProjectRoot "start.ps1"

try {
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = "powershell.exe"
    $shortcut.Arguments = "-ExecutionPolicy Bypass -File `"$startScript`""
    $shortcut.WorkingDirectory = $ProjectRoot
    $shortcut.Description = "Launch LocalMind AI Assistant"
    $shortcut.Save()
    Write-OK "Desktop shortcut created"
}
catch {
    Write-Warn "Could not create shortcut. Run start.ps1 manually."
}

# ── Done! ──────────────────────────────────────────────────────────
Write-Host ""
Write-Host "════════════════════════════════════════════" -ForegroundColor Magenta
Write-Host "   🧠 LocalMind is ready!" -ForegroundColor Green
Write-Host "════════════════════════════════════════════" -ForegroundColor Magenta
Write-Host ""
Write-Host "   To start:  .\start.ps1" -ForegroundColor White
Write-Host "   Or use the desktop shortcut." -ForegroundColor Gray
Write-Host ""
Write-Host "   📱 Remote access (phone):" -ForegroundColor White
Write-Host "   1. Install Tailscale on PC + phone: https://tailscale.com/download" -ForegroundColor Gray
Write-Host "   2. Start LocalMind" -ForegroundColor Gray
Write-Host "   3. Open http://<your-tailscale-ip>:8000 on your phone" -ForegroundColor Gray
Write-Host ""
