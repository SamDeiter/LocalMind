import * as vscode from 'vscode';
import * as http from 'http';
import * as https from 'https';
import { URL } from 'url';
import { extractContext, ExtractedContext } from './contextExtractor';

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
let chatPanel: vscode.WebviewPanel | undefined;
let lastTargetEditor: vscode.TextEditor | undefined;
let lastTargetSelection: vscode.Selection | undefined;

// ---------------------------------------------------------------------------
// Webview panel (singleton)
// ---------------------------------------------------------------------------

const CHAT_VIEW_TYPE = 'localmind.chatPanel';

function escapeHtml(input: string): string {
    return input
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

/**
 * Render a response string as minimal HTML: fenced ```code``` blocks become
 * <pre><code>, everything else becomes escaped <p> paragraphs split on
 * blank lines. No full markdown engine.
 */
function renderResponseHtml(text: string): string {
    const parts: string[] = [];
    const fence = /```([a-zA-Z0-9_+-]*)\n([\s\S]*?)```/g;
    let last = 0;
    let m: RegExpExecArray | null;
    while ((m = fence.exec(text)) !== null) {
        if (m.index > last) {
            parts.push(renderProse(text.slice(last, m.index)));
        }
        const lang = m[1] || '';
        const code = m[2];
        parts.push(
            `<pre class="code" data-lang="${escapeHtml(lang)}"><code>${escapeHtml(code)}</code></pre>`
        );
        last = m.index + m[0].length;
    }
    if (last < text.length) {
        parts.push(renderProse(text.slice(last)));
    }
    return parts.join('\n');
}

function renderProse(chunk: string): string {
    const trimmed = chunk.replace(/^\n+|\n+$/g, '');
    if (!trimmed) {
        return '';
    }
    return trimmed
        .split(/\n{2,}/)
        .map((p) => `<p>${escapeHtml(p).replace(/\n/g, '<br>')}</p>`)
        .join('\n');
}

/**
 * Extract the first fenced code block from a response, for the Apply button.
 */
function firstCodeBlock(text: string): string | null {
    const m = /```(?:[a-zA-Z0-9_+-]*)\n([\s\S]*?)```/.exec(text);
    return m ? m[1].replace(/\n+$/, '') : null;
}

function buildWebviewHtml(
    webview: vscode.Webview,
    title: string,
    bodyHtml: string,
    showApplyButton: boolean
): string {
    const nonce = Math.random().toString(36).slice(2) + Date.now().toString(36);
    const csp = [
        "default-src 'none'",
        "style-src 'unsafe-inline'",
        `script-src 'nonce-${nonce}'`,
    ].join('; ');
    const applyBar = showApplyButton
        ? `<div class="toolbar"><button id="apply">Apply Fix</button><span id="status"></span></div>`
        : '';
    return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="${csp}">
<title>${escapeHtml(title)}</title>
<style>
  body { font-family: var(--vscode-font-family); color: var(--vscode-foreground);
         background: var(--vscode-editor-background); padding: 12px; line-height: 1.45; }
  h2 { margin: 0 0 10px 0; font-size: 14px; font-weight: 600; }
  p { margin: 0 0 10px 0; }
  pre.code { background: var(--vscode-textCodeBlock-background, rgba(127,127,127,0.15));
             padding: 10px; border-radius: 4px; overflow-x: auto;
             font-family: var(--vscode-editor-font-family, monospace);
             font-size: 12.5px; white-space: pre; }
  .toolbar { position: sticky; top: 0; padding: 6px 0 10px 0;
             background: var(--vscode-editor-background); display: flex;
             align-items: center; gap: 10px; }
  button { background: var(--vscode-button-background);
           color: var(--vscode-button-foreground); border: none;
           padding: 4px 12px; border-radius: 2px; cursor: pointer; font-size: 12px; }
  button:hover { background: var(--vscode-button-hoverBackground); }
  #status { font-size: 12px; opacity: 0.8; }
</style>
</head>
<body>
  ${applyBar}
  <h2>${escapeHtml(title)}</h2>
  <div id="content">${bodyHtml}</div>
  <script nonce="${nonce}">
    const vscodeApi = acquireVsCodeApi();
    const applyBtn = document.getElementById('apply');
    if (applyBtn) {
      applyBtn.addEventListener('click', () => {
        vscodeApi.postMessage({ type: 'apply' });
      });
    }
    window.addEventListener('message', (event) => {
      const msg = event.data;
      if (msg && msg.type === 'status') {
        const s = document.getElementById('status');
        if (s) { s.textContent = msg.text || ''; }
      }
    });
  </script>
</body>
</html>`;
}

function ensureChatPanel(context: vscode.ExtensionContext): vscode.WebviewPanel {
    if (chatPanel) {
        chatPanel.reveal(vscode.ViewColumn.Beside, true);
        return chatPanel;
    }
    const panel = vscode.window.createWebviewPanel(
        CHAT_VIEW_TYPE,
        'LocalMind',
        { viewColumn: vscode.ViewColumn.Beside, preserveFocus: true },
        {
            enableScripts: true,
            retainContextWhenHidden: true,
            localResourceRoots: [],
        }
    );
    panel.onDidDispose(
        () => {
            chatPanel = undefined;
        },
        null,
        context.subscriptions
    );
    chatPanel = panel;
    return panel;
}

function showInChatPanel(
    context: vscode.ExtensionContext,
    title: string,
    responseText: string,
    applyHandler?: () => Promise<void> | void
): void {
    const panel = ensureChatPanel(context);
    const body = renderResponseHtml(responseText);
    const showApply = typeof applyHandler === 'function';
    panel.title = `LocalMind: ${title}`;
    panel.webview.html = buildWebviewHtml(panel.webview, title, body, showApply);

    // Fresh message subscription per render. Previous disposables are cleaned
    // up when the webview HTML is replaced (script context is torn down).
    const sub = panel.webview.onDidReceiveMessage(async (msg) => {
        if (msg?.type === 'apply' && applyHandler) {
            try {
                await applyHandler();
                panel.webview.postMessage({ type: 'status', text: 'Applied.' });
            } catch (err: unknown) {
                const m = err instanceof Error ? err.message : String(err);
                panel.webview.postMessage({ type: 'status', text: `Failed: ${m}` });
            }
        }
    });
    context.subscriptions.push(sub);
}

async function postChat(
    message: string,
    context?: ExtractedContext
): Promise<string> {
    const body: Record<string, unknown> = { message };
    if (context) {
        body.context = context;
    }
    const res = await httpRequest(`${getServerUrl()}/api/chat`, {
        method: 'POST',
        body,
    });
    if (res.statusCode < 200 || res.statusCode >= 300) {
        throw new Error(
            `Server returned status ${res.statusCode}: ${res.body.slice(0, 200)}`
        );
    }
    try {
        const data = JSON.parse(res.body);
        return String(data.response ?? data.message ?? res.body);
    } catch {
        return res.body;
    }
}

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

async function explainSelection(context: vscode.ExtensionContext): Promise<void> {
    const editor = vscode.window.activeTextEditor;
    if (!editor) {
        vscode.window.showWarningMessage('LocalMind: No active editor.');
        return;
    }
    const ctx = await extractContext(editor);
    if (!ctx.selection) {
        vscode.window.showWarningMessage('LocalMind: No text selected.');
        return;
    }

    const prompt = `Explain this code:\n\n${ctx.selection}`;
    const panel = ensureChatPanel(context);
    panel.title = 'LocalMind: Explain Selection';
    panel.webview.html = buildWebviewHtml(
        panel.webview,
        'Explain Selection',
        '<p><em>Thinking…</em></p>',
        false
    );

    try {
        const reply = await postChat(prompt, ctx);
        showInChatPanel(context, 'Explain Selection', reply);
    } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : String(err);
        showInChatPanel(context, 'Explain Selection', `Error: ${msg}`);
        vscode.window.showErrorMessage(`LocalMind: ${msg}`);
    }
}

async function fixSelection(context: vscode.ExtensionContext): Promise<void> {
    const editor = vscode.window.activeTextEditor;
    if (!editor) {
        vscode.window.showWarningMessage('LocalMind: No active editor.');
        return;
    }
    const ctx = await extractContext(editor);
    if (!ctx.selection) {
        vscode.window.showWarningMessage('LocalMind: No text selected.');
        return;
    }

    // Capture the editor + selection at command time so the Apply button
    // writes to the original location even if the user clicks around later.
    lastTargetEditor = editor;
    lastTargetSelection = editor.selection;

    const prompt = `Suggest a fix for this code:\n\n${ctx.selection}`;
    const panel = ensureChatPanel(context);
    panel.title = 'LocalMind: Fix Selection';
    panel.webview.html = buildWebviewHtml(
        panel.webview,
        'Fix Selection',
        '<p><em>Thinking…</em></p>',
        false
    );

    try {
        const reply = await postChat(prompt, ctx);
        const code = firstCodeBlock(reply);

        const applyHandler = code
            ? async () => {
                  const target = lastTargetEditor;
                  const sel = lastTargetSelection;
                  if (!target || !sel) {
                      throw new Error('Original selection lost.');
                  }
                  // If the document was closed, re-open it.
                  const stillOpen = vscode.window.visibleTextEditors.some(
                      (e) => e.document === target.document
                  );
                  const activeTarget = stillOpen
                      ? target
                      : await vscode.window.showTextDocument(
                            target.document,
                            { preserveFocus: false, viewColumn: target.viewColumn }
                        );
                  const ok = await activeTarget.edit((eb) => {
                      eb.replace(sel, code);
                  });
                  if (!ok) {
                      throw new Error('Edit was rejected by the editor.');
                  }
              }
            : undefined;

        showInChatPanel(context, 'Fix Selection', reply, applyHandler);
    } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : String(err);
        showInChatPanel(context, 'Fix Selection', `Error: ${msg}`);
        vscode.window.showErrorMessage(`LocalMind: ${msg}`);
    }
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
        vscode.commands.registerCommand('localmind.showStatus', showStatus),
        vscode.commands.registerCommand('localmind.explainSelection', () =>
            explainSelection(context)
        ),
        vscode.commands.registerCommand('localmind.fixSelection', () =>
            fixSelection(context)
        )
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
