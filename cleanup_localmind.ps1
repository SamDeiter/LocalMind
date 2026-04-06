# Cleanup LocalMind - PowerShell Script
# This script kills all redundant python instances and cleans up VRAM.

echo "🧹 Clearing redundant LocalMind processes..."

# Find all python processes running run.py or in the LocalMind directory
$processes = Get-Process python -ErrorAction SilentlyContinue | Where-Object { 
    $_.Path -like "*LocalMind*" -or $_.CommandLine -like "*run.py*"
}

if ($processes) {
    foreach ($p in $processes) {
        Write-Host "Killing Process ID: $($p.Id) ($($p.ProcessName))"
        Stop-Process -Id $p.Id -Force
    }
    echo "✅ Cleanup complete. Your VRAM should be clear."
} else {
    echo "✨ No redundant processes found."
}

echo "🚀 You can now start ONE fresh instance of LocalMind.bat."
