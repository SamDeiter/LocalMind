import * as vscode from 'vscode';
import * as http from 'http';
import * as https from 'https';
import { URL } from 'url';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

interface HttpResponse {
    statusCode: number;
    body: string;
}

function getServerUrl(): string {
    const config = vscode.workspace.getConfiguration('localmind');
    return config.get<string>('serverUrl', 'http://localhost:8000');
}

/**
 * Promise wrapper around Node.js http/https.request.
 * Supports GET and POST with JSON bodies. 5-second timeout.
 */
function httpRequest(
    url: string,
    options: { method?: string; body?: unknown } = {}
): Promise<HttpResponse> {
    return new Promise((resolve, reject) => {
        const parsed = new URL(url);
        const transport = parsed.protocol === 'https:' ? https : http;
        const method = options.method ?? 'GET';

        const reqOptions: http.RequestOptions = {
            hostname: parsed.hostname,
            port: parsed.port || (parsed.protocol === 'https:' ? 443 : 80),
            path: parsed.pathname + parsed.search,
            method,
            headers: {
                'Accept': 'application/json',
                'Content-Type': 'application/json',
            },
            timeout: 5000,
        };

        const req = transport.request(reqOptions, (res) => {
            const chunks: Buffer[] = [];
            res.on('data', (chunk: Buffer) => chunks.push(chunk));
            res.on('end', () => {
                resolve({
                    statusCode: res.statusCode ?? 0,
                    body: Buffer.concat(chunks).toString('utf-8'),
                });
            });
        });

        req.on('timeout', () => {
            req.destroy();
            reject(new Error('Request timed out after 5 seconds'));
        });

        req.on('error', (err) => reject(err));

        if (options.body !== undefined) {
            req.write(JSON.stringify(options.body));
        }
        req.end();
    });
}

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

let statusBarItem: vscode.StatusBarItem;
let outputChannel: vscode.OutputChannel;
let healthInterval: ReturnType<typeof setInterval> | undefined;

// ---------------------------------------------------------------------------
// Health check
// ---------------------------------------------------------------------------

async function checkHealth(): Promise<boolean> {
    try {
        const res = await httpRequest(`${getServerUrl()}/health`);
        const ok = res.statusCode >= 200 && res.statusCode < 300;
        updateStatusBar(ok);
        return ok;
    } catch {
        updateStatusBar(false);
        return false;
    }
}

function updateStatusBar(connected: boolean): void {
    if (connected) {
        statusBarItem.text = '$(check) LocalMind: Connected';
        statusBarItem.tooltip = 'LocalMind server is reachable';
        statusBarItem.backgroundColor = undefined;
    } else {
        statusBarItem.text = '$(error) LocalMind: Disconnected';
        statusBarItem.tooltip = 'Cannot reach LocalMind server';
        statusBarItem.backgroundColor = new vscode.ThemeColor(
            'statusBarItem.errorBackground'
        );
    }
}

// ---------------------------------------------------------------------------
// Commands
// ---------------------------------------------------------------------------

async function sendSelection(): Promise<void> {
    const editor = vscode.window.activeTextEditor;
    if (!editor) {
        vscode.window.showWarningMessage('No active editor.');
        return;
    }

    const selection = editor.document.getText(editor.selection);
    if (!selection) {
        vscode.window.showWarningMessage('No text selected.');
        return;
    }

    const fileName = editor.document.fileName;
    const languageId = editor.document.languageId;
    const prompt = `Analyze the following ${languageId} code from ${fileName}:\n\n\`\`\`${languageId}\n${selection}\n\`\`\``;

    outputChannel.show(true);
    outputChannel.appendLine('--- Send Selection ---');
    outputChannel.appendLine(`File: ${fileName}`);
    outputChannel.appendLine('');

    try {
        const res = await httpRequest(`${getServerUrl()}/api/chat`, {
            method: 'POST',
            body: { message: prompt },
        });

        if (res.statusCode >= 200 && res.statusCode < 300) {
            const data = JSON.parse(res.body);
            const reply = data.response ?? data.message ?? res.body;
            outputChannel.appendLine(String(reply));
        } else {
            outputChannel.appendLine(`Server returned status ${res.statusCode}`);
            outputChannel.appendLine(res.body);
        }
    } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : String(err);
        outputChannel.appendLine(`Error: ${msg}`);
        vscode.window.showErrorMessage(`LocalMind: ${msg}`);
    }

    outputChannel.appendLine('');
}

async function askQuestion(): Promise<void> {
    const question = await vscode.window.showInputBox({
        prompt: 'Ask LocalMind a question',
        placeHolder: 'e.g. How do I write a unit test for this function?',
    });

    if (!question) {
        return;
    }

    outputChannel.show(true);
    outputChannel.appendLine('--- Ask a Question ---');
    outputChannel.appendLine(`Q: ${question}`);
    outputChannel.appendLine('');

    try {
        const res = await httpRequest(`${getServerUrl()}/api/chat`, {
            method: 'POST',
            body: { message: question },
        });

        if (res.statusCode >= 200 && res.statusCode < 300) {
            const data = JSON.parse(res.body);
            const reply = data.response ?? data.message ?? res.body;
            outputChannel.appendLine(String(reply));
        } else {
            outputChannel.appendLine(`Server returned status ${res.statusCode}`);
            outputChannel.appendLine(res.body);
        }
    } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : String(err);
        outputChannel.appendLine(`Error: ${msg}`);
        vscode.window.showErrorMessage(`LocalMind: ${msg}`);
    }

    outputChannel.appendLine('');
}

async function registerProject(): Promise<void> {
    const folders = vscode.workspace.workspaceFolders;
    if (!folders || folders.length === 0) {
        vscode.window.showWarningMessage('No workspace folder open.');
        return;
    }

    const folder = folders[0];
    const projectPath = folder.uri.fsPath;
    const projectName = folder.name;

    try {
        const res = await httpRequest(`${getServerUrl()}/api/hub/projects`, {
            method: 'POST',
            body: { name: projectName, path: projectPath },
        });

        if (res.statusCode >= 200 && res.statusCode < 300) {
            vscode.window.showInformationMessage(
                `Registered project "${projectName}" with LocalMind.`
            );
        } else {
            vscode.window.showErrorMessage(
                `Failed to register project (status ${res.statusCode}).`
            );
        }
    } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : String(err);
        vscode.window.showErrorMessage(`LocalMind: ${msg}`);
    }
}

async function showStatus(): Promise<void> {
    const serverUrl = getServerUrl();
    let statusParts: string[] = [];

    // Health
    try {
        const healthRes = await httpRequest(`${serverUrl}/health`);
        if (healthRes.statusCode >= 200 && healthRes.statusCode < 300) {
            statusParts.push('Server: Online');
        } else {
            statusParts.push(`Server: Error (${healthRes.statusCode})`);
        }
    } catch {
        statusParts.push('Server: Offline');
    }

    statusParts.push(`URL: ${serverUrl}`);

    // Models
    try {
        const modelsRes = await httpRequest(`${serverUrl}/api/models`);
        if (modelsRes.statusCode >= 200 && modelsRes.statusCode < 300) {
            const data = JSON.parse(modelsRes.body);
            const models: string[] = Array.isArray(data)
                ? data.map((m: { id?: string; name?: string }) => m.id ?? m.name ?? String(m))
                : Array.isArray(data.models)
                  ? data.models.map((m: { id?: string; name?: string }) => m.id ?? m.name ?? String(m))
                  : [];
            if (models.length > 0) {
                statusParts.push(`Models: ${models.join(', ')}`);
            } else {
                statusParts.push('Models: none detected');
            }
        }
    } catch {
        statusParts.push('Models: unavailable');
    }

    vscode.window.showInformationMessage(statusParts.join(' | '));
}

// ---------------------------------------------------------------------------
// Activation / Deactivation
// ---------------------------------------------------------------------------

export function activate(context: vscode.ExtensionContext): void {
    // Output channel
    outputChannel = vscode.window.createOutputChannel('LocalMind');
    context.subscriptions.push(outputChannel);

    // Status bar
    statusBarItem = vscode.window.createStatusBarItem(
        vscode.StatusBarAlignment.Right,
        100
    );
    statusBarItem.command = 'localmind.showStatus';
    statusBarItem.text = '$(sync~spin) LocalMind';
    statusBarItem.show();
    context.subscriptions.push(statusBarItem);

    // Register commands
    context.subscriptions.push(
        vscode.commands.registerCommand('localmind.sendSelection', sendSelection),
        vscode.commands.registerCommand('localmind.askQuestion', askQuestion),
        vscode.commands.registerCommand('localmind.registerProject', registerProject),
        vscode.commands.registerCommand('localmind.showStatus', showStatus)
    );

    // Auto-connect
    const config = vscode.workspace.getConfiguration('localmind');
    if (config.get<boolean>('autoConnect', true)) {
        checkHealth();
    } else {
        updateStatusBar(false);
    }

    // Poll health every 30 seconds
    healthInterval = setInterval(() => {
        checkHealth();
    }, 30_000);

    context.subscriptions.push({
        dispose: () => {
            if (healthInterval !== undefined) {
                clearInterval(healthInterval);
                healthInterval = undefined;
            }
        },
    });
}

export function deactivate(): void {
    if (healthInterval !== undefined) {
        clearInterval(healthInterval);
        healthInterval = undefined;
    }
}
