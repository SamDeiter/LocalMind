
# LocalMind Storage Migration Script (migrate_models.ps1)

$SourcePath = "$env:USERPROFILE\.ollama\models"
$DestPath = "D:\Models"

# 1. Check if Ollama is running
$OllamaProc = Get-Process ollama -ErrorAction SilentlyContinue
if ($OllamaProc) {
    Write-Error "Ollama is still running! Please QUIT Ollama from the system tray first."
    exit 1
}

# 2. Ensure destination exists
if (!(Test-Path $DestPath)) {
    Write-Host "Creating $DestPath..."
    New-Item -ItemType Directory -Path $DestPath | Out-Null
}

# 3. Move the model data
Write-Host "Moving model data from $SourcePath to $DestPath..."
try {
    # We move the contents (blobs, manifests) to the new root
    Move-Item -Path "$SourcePath\*" -Destination $DestPath -Force
    Write-Host "✅ Files moved successfully."
} catch {
    Write-Error "Failed to move files: $($_.Exception.Message)"
    exit 1
}

# 4. Set the environment variable permanently for the user
Write-Host "Updating OLLAMA_MODELS environment variable..."
[System.Environment]::SetEnvironmentVariable('OLLAMA_MODELS', $DestPath, 'User')

Write-Host ""
Write-Host "🎉 SUCCESS!"
Write-Host "Your models are now on the D: drive."
Write-Host "Please RESTART the Ollama app (tray) to finish the migration."
Write-Host ""
