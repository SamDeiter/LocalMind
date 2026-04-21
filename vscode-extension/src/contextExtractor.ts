/**
 * contextExtractor.ts (Phase E)
 *
 * Extracts context from the active text editor for LocalMind commands:
 *   - file path (relative to workspace root when possible)
 *   - languageId
 *   - current selection text
 *   - 10 lines before + 10 lines after the selection
 *   - first git-blame hit for the selection start line (optional, never throws)
 *   - workspace root
 *
 * Git blame is best-effort: if the file is not in a repo, git is missing,
 * or the command fails for any reason, gitBlame is null.
 */
import * as vscode from 'vscode';
import * as path from 'path';
import { exec } from 'child_process';
import { promisify } from 'util';

const execAsync = promisify(exec);

export interface GitBlameInfo {
    author: string;
    commit: string;
    date: string;
}

export interface ExtractedContext {
    filePath: string;
    languageId: string;
    selection: string;
    surroundingLines: string;
    gitBlame?: GitBlameInfo | null;
    workspaceRoot: string;
}

const SURROUND_LINES = 10;

/**
 * Parse the porcelain output of `git blame -L n,n --porcelain <file>`.
 * The first line is `<commit-sha> <orig> <final> <group-size>`.
 * Subsequent header lines include `author <name>` and `author-time <unix>`.
 * Returns null on any parse failure.
 */
function parseBlamePorcelain(output: string): GitBlameInfo | null {
    if (!output) {
        return null;
    }
    const lines = output.split(/\r?\n/);
    if (lines.length === 0) {
        return null;
    }

    const header = lines[0].split(' ');
    const commit = header[0];
    if (!commit || commit.length < 7) {
        return null;
    }

    let author = 'Unknown';
    let authorTime: number | null = null;
    let authorTz = '+0000';

    for (let i = 1; i < lines.length; i++) {
        const line = lines[i];
        if (line.startsWith('author ')) {
            author = line.slice(7).trim();
        } else if (line.startsWith('author-time ')) {
            const t = parseInt(line.slice(12).trim(), 10);
            if (!Number.isNaN(t)) {
                authorTime = t;
            }
        } else if (line.startsWith('author-tz ')) {
            authorTz = line.slice(10).trim();
        } else if (line.startsWith('\t')) {
            // Reached the actual source content line; headers are done.
            break;
        }
    }

    let date = '';
    if (authorTime !== null) {
        try {
            date = new Date(authorTime * 1000).toISOString();
        } catch {
            date = String(authorTime);
        }
    }

    return {
        author,
        commit: commit.slice(0, 12),
        date: date || authorTz,
    };
}

/**
 * Best-effort git blame. Never throws. Returns null when unavailable.
 */
async function safeGitBlame(
    absFilePath: string,
    lineNumber1Based: number,
    cwd: string
): Promise<GitBlameInfo | null> {
    try {
        const { stdout } = await execAsync(
            `git blame -L ${lineNumber1Based},${lineNumber1Based} --porcelain -- "${absFilePath}"`,
            {
                cwd,
                timeout: 3000,
                maxBuffer: 1024 * 1024,
                windowsHide: true,
            }
        );
        return parseBlamePorcelain(stdout);
    } catch {
        return null;
    }
}

/**
 * Extract context from a text editor for LocalMind prompts.
 */
export async function extractContext(
    editor: vscode.TextEditor
): Promise<ExtractedContext> {
    const doc = editor.document;
    const absFilePath = doc.uri.fsPath;

    // Resolve workspace root for this document, fall back to the first folder,
    // then to the file's directory.
    const folder = vscode.workspace.getWorkspaceFolder(doc.uri);
    const workspaceRoot =
        folder?.uri.fsPath ??
        vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ??
        path.dirname(absFilePath);

    // Path relative to workspace root when possible.
    let filePath = absFilePath;
    try {
        const rel = path.relative(workspaceRoot, absFilePath);
        if (rel && !rel.startsWith('..') && !path.isAbsolute(rel)) {
            filePath = rel.split(path.sep).join('/');
        }
    } catch {
        filePath = absFilePath;
    }

    // Selection text (empty string when no selection).
    const selection = doc.getText(editor.selection);

    // Surrounding lines: 10 before start, 10 after end, clamped to document edges.
    const startLine = Math.max(0, editor.selection.start.line - SURROUND_LINES);
    const endLine = Math.min(
        doc.lineCount - 1,
        editor.selection.end.line + SURROUND_LINES
    );
    const surroundingRange = new vscode.Range(
        startLine,
        0,
        endLine,
        doc.lineAt(endLine).text.length
    );
    const surroundingLines = doc.getText(surroundingRange);

    // Git blame on the selection start line (1-based for git).
    const blameLine = editor.selection.start.line + 1;
    const gitBlame = await safeGitBlame(absFilePath, blameLine, workspaceRoot);

    return {
        filePath,
        languageId: doc.languageId,
        selection,
        surroundingLines,
        gitBlame,
        workspaceRoot,
    };
}
