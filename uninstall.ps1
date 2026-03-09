# LocalMind Uninstall Script for Windows
# Run: .\uninstall.ps1

Write-Host ""
Write-Host "  ================================================" -ForegroundColor Yellow
Write-Host "    LocalMind Uninstaller" -ForegroundColor White
Write-Host "  ================================================" -ForegroundColor Yellow
Write-Host ""

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# ── Step 1: Stop Ollama ───────────────────────────────────────────────────
Write-Host "[1/4] Stopping Ollama service..." -ForegroundColor Yellow
$ollamaProcess = Get-Process -Name "ollama*" -ErrorAction SilentlyContinue
if ($ollamaProcess) {
    Stop-Process -Name "ollama*" -Force -ErrorAction SilentlyContinue
    Write-Host "  Ollama stopped." -ForegroundColor Green
}
else {
    Write-Host "  Ollama was not running." -ForegroundColor DarkGray
}

# ── Step 2: Optionally remove models ─────────────────────────────────────
Write-Host ""
$removeModels = Read-Host "  Remove downloaded AI models (~20GB)? (y/N)"
if ($removeModels -eq "y" -or $removeModels -eq "Y") {
    Write-Host "[2/4] Removing models..." -ForegroundColor Yellow
    $ollamaDir = Join-Path $env:USERPROFILE ".ollama"
    if (Test-Path $ollamaDir) {
        Remove-Item -Path (Join-Path $ollamaDir "models") -Recurse -Force -ErrorAction SilentlyContinue
        Write-Host "  Models removed. Reclaimed disk space!" -ForegroundColor Green
    }
}
else {
    Write-Host "[2/4] Keeping models (you can reuse them later)." -ForegroundColor DarkGray
}

# ── Step 3: Remove Python virtual environment ─────────────────────────────
Write-Host "[3/4] Removing Python virtual environment..." -ForegroundColor Yellow
$venvPath = Join-Path $ProjectDir "venv"
if (Test-Path $venvPath) {
    Remove-Item -Path $venvPath -Recurse -Force
    Write-Host "  Virtual environment removed." -ForegroundColor Green
}
else {
    Write-Host "  No virtual environment found." -ForegroundColor DarkGray
}

# ── Step 4: Remove SQLite database ─────────────────────────────────────────
$dbPath = Join-Path $ProjectDir "backend\conversations.db"
if (Test-Path $dbPath) {
    $removeDb = Read-Host "  Delete conversation history? (y/N)"
    if ($removeDb -eq "y" -or $removeDb -eq "Y") {
        Remove-Item -Path $dbPath -Force
        Write-Host "  Conversation history deleted." -ForegroundColor Green
    }
}

# ── Step 5: Remove desktop shortcut ────────────────────────────────────────
Write-Host "[4/4] Removing desktop shortcut..." -ForegroundColor Yellow
$desktopPath = [Environment]::GetFolderPath("Desktop")
$shortcutPath = Join-Path $desktopPath "LocalMind.lnk"
if (Test-Path $shortcutPath) {
    Remove-Item -Path $shortcutPath -Force
    Write-Host "  Shortcut removed." -ForegroundColor Green
}
else {
    Write-Host "  No shortcut found." -ForegroundColor DarkGray
}

# ── Done! ─────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "  ================================================" -ForegroundColor Green
Write-Host "    Uninstall Complete!" -ForegroundColor White
Write-Host "  ================================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Note: The project folder has been kept in place." -ForegroundColor DarkGray
Write-Host "  To reinstall later, run: .\install.ps1" -ForegroundColor DarkGray
Write-Host ""
