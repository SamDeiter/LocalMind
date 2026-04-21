import os
from pathlib import Path

def main():
    root = Path(__file__).resolve().parent
    ps1_path = root / "install.ps1"
    if not ps1_path.exists():
        print("install.ps1 not found")
        return

    content = ps1_path.read_text(encoding="utf-8")
    
    if "gemma4:e2b" not in content:
        old_pull = "    & ollama pull qwen2.5-coder:32b\n    Write-Host '  [OK] Model ready' -ForegroundColor Green"
        new_pull = """    & ollama pull qwen2.5-coder:32b
    Write-Host '  Pulling gemma4:e2b (Micro local model)...' -ForegroundColor Gray
    & ollama pull gemma4:e2b
    Write-Host '  [OK] Models ready' -ForegroundColor Green"""
        
        content = content.replace(old_pull, new_pull)
        
        old_echo_pull = "Write-Host '  Pulling qwen2.5-coder:32b (~20GB) to D: drive...' -ForegroundColor Gray"
        new_echo_pull = "Write-Host '  Pulling qwen2.5-coder:32b and gemma4:e2b to D: drive...' -ForegroundColor Gray"
        
        content = content.replace(old_echo_pull, new_echo_pull)
        
        ps1_path.write_text(content, encoding="utf-8")
        print("install.ps1 updated successfully.")
    else:
        print("install.ps1 already updated.")

if __name__ == "__main__":
    main()
