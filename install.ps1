# LocalMind - Installation Script
$ErrorActionPreference = 'Stop'
$ProjectRoot = $PSScriptRoot
$Workspace = 'C:\LocalMind_Workspace'
$VenvPath = Join-Path $ProjectRoot 'venv'

Write-Host ''
Write-Host '============================================' -ForegroundColor Magenta
Write-Host '   LocalMind - Installation' -ForegroundColor White
Write-Host '============================================' -ForegroundColor Magenta

# Step 1: Check for Ollama
Write-Host ''
Write-Host '[STEP] Checking for Ollama...' -ForegroundColor Cyan
$ollamaPath = Get-Command ollama -ErrorAction SilentlyContinue
if ($ollamaPath) {
    $version = & ollama --version 2>&1
    Write-Host ('  [OK] Ollama found: ' + $version) -ForegroundColor Green
} else {
    Write-Host '  [WARN] Ollama not found. Installing...' -ForegroundColor Yellow
    $installerUrl = 'https://ollama.com/download/OllamaSetup.exe'
    $installerPath = Join-Path $env:TEMP 'OllamaSetup.exe'
    try {
        Invoke-WebRequest -Uri $installerUrl -OutFile $installerPath -UseBasicParsing
        Write-Host '  Running installer (follow the prompts)...' -ForegroundColor Gray
        Start-Process -FilePath $installerPath -Wait
        $env:Path = [System.Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [System.Environment]::GetEnvironmentVariable('Path','User')
        Write-Host '  [OK] Ollama installed' -ForegroundColor Green
    } catch {
        Write-Host '  [ERR] Failed to install Ollama. Get it from https://ollama.com' -ForegroundColor Red
        exit 1
    }
}

# Step 2: Set Ollama model storage to D: drive (more space)
Write-Host ''
Write-Host '[STEP] Configuring Ollama model storage on D: drive...' -ForegroundColor Cyan
$modelDir = 'D:\OllamaModels'
if (-not (Test-Path $modelDir)) {
    New-Item -ItemType Directory -Path $modelDir -Force | Out-Null
}
[System.Environment]::SetEnvironmentVariable('OLLAMA_MODELS', $modelDir, 'User')
$env:OLLAMA_MODELS = $modelDir
Write-Host ('  [OK] Models will be stored at: ' + $modelDir) -ForegroundColor Green

# Step 3: Start Ollama
Write-Host ''
Write-Host '[STEP] Ensuring Ollama is running...' -ForegroundColor Cyan
try {
    $null = Invoke-RestMethod -Uri 'http://localhost:11434/api/tags' -Method Get -TimeoutSec 3 -ErrorAction Stop
    Write-Host '  [OK] Ollama is running' -ForegroundColor Green
} catch {
    Write-Host '  Starting Ollama...' -ForegroundColor Gray
    Start-Process ollama -ArgumentList 'serve' -WindowStyle Hidden
    Start-Sleep -Seconds 5
    Write-Host '  [OK] Ollama started' -ForegroundColor Green
}

# Step 4: Pull model
Write-Host ''
Write-Host '[STEP] Pulling AI model (this takes a while on first run)...' -ForegroundColor Cyan
Write-Host '  Pulling qwen2.5-coder:32b (~20GB) to D: drive...' -ForegroundColor Gray
try {
    & ollama pull qwen2.5-coder:32b
    Write-Host '  [OK] Model ready' -ForegroundColor Green
} catch {
    Write-Host '  [WARN] Model pull failed. Run later: ollama pull qwen2.5-coder:32b' -ForegroundColor Yellow
}

# Step 5: Python venv
Write-Host ''
Write-Host '[STEP] Setting up Python environment...' -ForegroundColor Cyan
if (-not (Test-Path $VenvPath)) {
    python -m venv $VenvPath
    Write-Host '  [OK] Virtual environment created' -ForegroundColor Green
} else {
    Write-Host '  [OK] Virtual environment already exists' -ForegroundColor Green
}
$pipPath = Join-Path $VenvPath 'Scripts' 'pip.exe'
$reqPath = Join-Path $ProjectRoot 'requirements.txt'
& $pipPath install -r $reqPath --quiet
Write-Host '  [OK] Python dependencies installed' -ForegroundColor Green

# Step 6: Workspace at C: root
Write-Host ''
Write-Host '[STEP] Creating workspace...' -ForegroundColor Cyan
if (-not (Test-Path $Workspace)) {
    New-Item -ItemType Directory -Path $Workspace | Out-Null
    Write-Host ('  [OK] Workspace: ' + $Workspace) -ForegroundColor Green
} else {
    Write-Host ('  [OK] Workspace exists: ' + $Workspace) -ForegroundColor Green
}

# Step 7: Desktop shortcut
Write-Host ''
Write-Host '[STEP] Creating desktop shortcut...' -ForegroundColor Cyan
try {
    $desktopPath = [Environment]::GetFolderPath('Desktop')
    $shortcutPath = Join-Path $desktopPath 'LocalMind.lnk'
    $startScript = Join-Path $ProjectRoot 'start.ps1'
    $wshell = New-Object -ComObject WScript.Shell
    $sc = $wshell.CreateShortcut($shortcutPath)
    $sc.TargetPath = 'powershell.exe'
    $sc.Arguments = ('-ExecutionPolicy Bypass -File ' + [char]34 + $startScript + [char]34)
    $sc.WorkingDirectory = $ProjectRoot
    $sc.Description = 'Launch LocalMind AI Assistant'
    $sc.Save()
    Write-Host '  [OK] Desktop shortcut created' -ForegroundColor Green
} catch {
    Write-Host '  [WARN] Could not create shortcut.' -ForegroundColor Yellow
}

Write-Host ''
Write-Host '============================================' -ForegroundColor Magenta
Write-Host '   LocalMind is ready!' -ForegroundColor Green
Write-Host '============================================' -ForegroundColor Magenta
Write-Host ''
Write-Host '   To start:  .\start.ps1' -ForegroundColor White
Write-Host ''
