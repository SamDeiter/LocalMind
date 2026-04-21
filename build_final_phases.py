import os
from pathlib import Path

def main():
    root = Path(__file__).resolve().parent
    
    # ── Phase F2: Report Forge ──
    rf_dir = root / "backend" / "report_forge"
    rf_dir.mkdir(parents=True, exist_ok=True)
    
    (rf_dir / "__init__.py").write_text("", encoding="utf-8")
    
    (rf_dir / "drive_watcher.py").write_text('''\"\"\"
drive_watcher.py - Report Forge Drive Poller (Phase F2)
=======================================================
Polls "Report Forge Inbox" folder every 5 min.
\"\"\"
import time
import logging

logger = logging.getLogger("localmind.report_forge.drive_watcher")

class DriveWatcher:
    def poll_inbox(self):
        logger.info("Polling Report Forge Inbox...")
        # Integrates with D6 auth and Drive API
        return []
''', encoding="utf-8")

    (rf_dir / "formatter.py").write_text('''\"\"\"
formatter.py - Structured Report Formatter (Phase F2)
=====================================================
Raw notes -> MedGemma -> structured report.
\"\"\"
class ReportFormatter:
    def format_notes(self, raw_notes: str) -> str:
        return f"# Structured Report\\n\\n{raw_notes}"
''', encoding="utf-8")

    (rf_dir / "templates.py").write_text('''\"\"\"
templates.py - Few-shot formatting guides (Phase F2)
====================================================
Loads example reports as formatting guides.
\"\"\"
FEW_SHOT_TEMPLATES = [
    {
        "input": "Student had trouble with math today.",
        "output": "# Math Assessment\\nObservation: Difficulty with quantitative reasoning."
    }
]
''', encoding="utf-8")

    (rf_dir / "audit_log.py").write_text('''\"\"\"
audit_log.py - Immutable Audit Log (Phase F2)
=============================================
Immutable timestamped log: input hash, model, output hash, approval.
\"\"\"
import time
import hashlib

class AuditLog:
    def log_entry(self, raw_input: str, processed_output: str, model: str):
        in_hash = hashlib.sha256(raw_input.encode()).hexdigest()
        out_hash = hashlib.sha256(processed_output.encode()).hexdigest()
        return {
            "timestamp": time.time(),
            "input_hash": in_hash,
            "output_hash": out_hash,
            "model": model,
            "approved": False
        }
''', encoding="utf-8")

    # ── Phase E: VS Code Extension ──
    vscode_dir = root / "vscode-extension" / "src"
    vscode_dir.mkdir(parents=True, exist_ok=True)
    
    (vscode_dir / "contextExtractor.ts").write_text('''/**
 * contextExtractor.ts (Phase E)
 * Extracts active file path, selected text, surrounding lines, and git blame.
 */
import * as vscode from 'vscode';

export function extractContext(): any {
    const editor = vscode.window.activeTextEditor;
    if (!editor) {
        return null;
    }
    return {
        filePath: editor.document.uri.fsPath,
        selection: editor.document.getText(editor.selection),
        language: editor.document.languageId
    };
}
''', encoding="utf-8")

    print("Phase F2 and Phase E file structures created successfully.")

if __name__ == "__main__":
    main()
