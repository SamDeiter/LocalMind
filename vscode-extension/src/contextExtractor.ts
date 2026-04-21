/**
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
