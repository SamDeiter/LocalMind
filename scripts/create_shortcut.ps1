# Create Desktop Shortcut for LocalMind
# Run this once: powershell -ExecutionPolicy Bypass -File scripts\create_shortcut.ps1

$WScriptShell = New-Object -ComObject WScript.Shell
$Shortcut = $WScriptShell.CreateShortcut("$env:USERPROFILE\Desktop\LocalMind.lnk")
$Shortcut.TargetPath = "$PSScriptRoot\..\LocalMind.bat"
$Shortcut.WorkingDirectory = "$PSScriptRoot\.."
$Shortcut.Description = "Launch LocalMind AI Assistant"
$Shortcut.WindowStyle = 7  # Minimized
$Shortcut.Save()

Write-Host ""
Write-Host "  Desktop shortcut created!" -ForegroundColor Green
Write-Host "  Double-click 'LocalMind' on your desktop to start." -ForegroundColor Cyan
Write-Host ""
