# LocalMind Quick Launch Script
# Run: .\start.ps1

Write-Host ""
Write-Host "  Starting LocalMind..." -ForegroundColor Cyan
Write-Host ""

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# ── Start Ollama if not running ───────────────────────────────────────────
$ollamaRunning = Get-Process -Name "ollama" -ErrorAction SilentlyContinue
if (-not $ollamaRunning) {
    Write-Host "  Starting Ollama service..." -ForegroundColor Yellow
    Start-Process ollama -ArgumentList "serve" -WindowStyle Hidden
    Start-Sleep -Seconds 2
    Write-Host "  Ollama started." -ForegroundColor Green
}
else {
    Write-Host "  Ollama already running." -ForegroundColor Green
}

# ── Start the Python backend ─────────────────────────────────────────────
Write-Host "  Launching LocalMind server on http://localhost:8000" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Press Ctrl+C to stop the server." -ForegroundColor DarkGray
Write-Host ""

# Open browser after a short delay
Start-Job -ScriptBlock {
    Start-Sleep -Seconds 2
    Start-Process "http://localhost:8000"
} | Out-Null

# Run the server
$venvPython = Join-Path $ProjectDir "venv\Scripts\python.exe"
if (Test-Path $venvPython) {
    & $venvPython (Join-Path $ProjectDir "backend\server.py")
}
else {
    python (Join-Path $ProjectDir "backend\server.py")
}
