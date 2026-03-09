# LocalMind Install Script for Windows
# Run: .\install.ps1

Write-Host ""
Write-Host "  ================================================" -ForegroundColor Cyan
Write-Host "    LocalMind Installer" -ForegroundColor White
Write-Host "    Your Private AI Coding Assistant" -ForegroundColor DarkGray
Write-Host "  ================================================" -ForegroundColor Cyan
Write-Host ""

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# ── Step 1: Check Python ──────────────────────────────────────────────────
Write-Host "[1/5] Checking Python..." -ForegroundColor Yellow
try {
    $pythonVersion = python --version 2>&1
    Write-Host "  Found: $pythonVersion" -ForegroundColor Green
} catch {
    Write-Host "  ERROR: Python not found. Please install Python 3.10+ from https://python.org" -ForegroundColor Red
    exit 1
}

# ── Step 2: Check/Install Ollama ──────────────────────────────────────────
Write-Host "[2/5] Checking Ollama..." -ForegroundColor Yellow
$ollamaPath = Get-Command ollama -ErrorAction SilentlyContinue
if ($ollamaPath) {
    Write-Host "  Found: Ollama is installed" -ForegroundColor Green
} else {
    Write-Host "  Ollama not found. Downloading installer..." -ForegroundColor Cyan
    $installerUrl = "https://ollama.com/download/OllamaSetup.exe"
    $installerPath = Join-Path $env:TEMP "OllamaSetup.exe"
    
    Invoke-WebRequest -Uri $installerUrl -OutFile $installerPath -UseBasicParsing
    Write-Host "  Running Ollama installer..." -ForegroundColor Cyan
    Start-Process -FilePath $installerPath -Wait
    
    # Refresh PATH
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
    
    Write-Host "  Ollama installed successfully!" -ForegroundColor Green
}

# ── Step 3: Pull the model ────────────────────────────────────────────────
Write-Host "[3/5] Pulling Qwen 2.5 Coder 32B model (~20GB, this may take a while)..." -ForegroundColor Yellow
Write-Host "  This is a one-time download." -ForegroundColor DarkGray

# Make sure Ollama is running
$ollamaRunning = Get-Process -Name "ollama" -ErrorAction SilentlyContinue
if (-not $ollamaRunning) {
    Write-Host "  Starting Ollama service..." -ForegroundColor Cyan
    Start-Process ollama -ArgumentList "serve" -WindowStyle Hidden
    Start-Sleep -Seconds 3
}

ollama pull qwen2.5-coder:32b
Write-Host "  Model downloaded!" -ForegroundColor Green

# ── Step 4: Set up Python virtual environment ─────────────────────────────
Write-Host "[4/5] Setting up Python environment..." -ForegroundColor Yellow
$venvPath = Join-Path $ProjectDir "venv"

if (-not (Test-Path $venvPath)) {
    python -m venv $venvPath
}

$pipPath = Join-Path $venvPath "Scripts\pip.exe"
& $pipPath install -r (Join-Path $ProjectDir "backend\requirements.txt") --quiet
Write-Host "  Python dependencies installed!" -ForegroundColor Green

# ── Step 5: Create Desktop Shortcut ───────────────────────────────────────
Write-Host "[5/5] Creating desktop shortcut..." -ForegroundColor Yellow
$desktopPath = [Environment]::GetFolderPath("Desktop")
$shortcutPath = Join-Path $desktopPath "LocalMind.lnk"
$startScript = Join-Path $ProjectDir "start.ps1"

$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut($shortcutPath)
$Shortcut.TargetPath = "powershell.exe"
$Shortcut.Arguments = "-ExecutionPolicy Bypass -File `"$startScript`""
$Shortcut.WorkingDirectory = $ProjectDir
$Shortcut.Description = "Launch LocalMind AI Assistant"
$Shortcut.Save()
Write-Host "  Desktop shortcut created!" -ForegroundColor Green

# ── Done! ─────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "  ================================================" -ForegroundColor Green
Write-Host "    Installation Complete!" -ForegroundColor White
Write-Host "  ================================================" -ForegroundColor Green
Write-Host ""
Write-Host "  To start LocalMind, either:" -ForegroundColor White
Write-Host "    1. Double-click the 'LocalMind' shortcut on your Desktop" -ForegroundColor DarkGray
Write-Host "    2. Run: .\start.ps1" -ForegroundColor DarkGray
Write-Host ""
Write-Host "  The UI will open at: http://localhost:8000" -ForegroundColor Cyan
Write-Host ""
