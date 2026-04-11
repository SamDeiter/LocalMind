/**
 * SSE streaming — fetch + ReadableStream parsing for the /api/chat endpoint.
 * Handles retry logic and calls back for each event type so the UI layer
 * can stay in chat.js.
 */

import { API } from "./state.js";

const MAX_RETRIES = 2;

/**
 * Stream a chat request and invoke callbacks for each SSE event.
 *
 * @param {Object} body        – JSON body sent to POST /api/chat
 * @param {Object} callbacks   – event handlers
 * @param {function} callbacks.onToken       – (token: string, fullText: string) => void
 * @param {function} callbacks.onToolCall    – (toolCall: object) => void
 * @param {function} callbacks.onApproval    – (approvalRequest: object) => void
 * @param {function} callbacks.onToolResult  – (result: object) => void
 * @param {function} callbacks.onThinking    – (thinking: object) => void
 * @param {function} callbacks.onEstimate    – (estimate: object) => void
 * @param {function} callbacks.onAnalytics   – (analytics: object) => void
 * @param {function} callbacks.onTitleUpdate  – (data: {conversation_id, title}) => void
 * @param {function} callbacks.onDone        – (evt: object) => void
 * @param {function} callbacks.onError       – (error: string) => void
 * @param {function} [callbacks.onReconnecting] – (attempt: number, maxRetries: number) => void
 * @param {AbortSignal} [signal] – optional AbortSignal for cancellation
 * @returns {Promise<string>} the accumulated full text from all token events
 */
export async function streamChat(body, callbacks, signal) {
  const {
    onToken,
    onToolCall,
    onApproval,
    onToolResult,
    onThinking,
    onEstimate,
    onAnalytics,
    onTitleUpdate,
    onDone,
    onError,
    onReconnecting,
  } = callbacks;

  let fullText = "";

  for (let attempt = 0; attempt <= MAX_RETRIES; attempt++) {
    try {
      console.log("[LocalMind] Sending chat request (attempt %d):", attempt + 1, {
        model: body.model,
        msg_len: body.message?.length,
        conv_id: body.conversation_id,
      });

      // Reset state for this attempt so partial data doesn't double up
      fullText = "";
      let buffer = "";
      let chunkCount = 0;

      const resp = await fetch(`${API}/api/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        signal,
      });
      console.log("[LocalMind] Fetch response status:", resp.status, resp.statusText);

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();

      while (true) {
        const { done, value } = await reader.read();
        if (done) {
          console.log("[LocalMind] Stream ended. Total chunks:", chunkCount);
          break;
        }

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop();

        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          const raw = line.slice(6).trim();
          if (!raw || raw === "[DONE]") continue;

          let evt;
          try {
            evt = JSON.parse(raw);
          } catch {
            continue;
          }
          chunkCount++;
          console.log("[LocalMind] SSE event:", JSON.stringify(evt).substring(0, 120));

          if (evt.token) {
            fullText += evt.token;
            console.log("[LocalMind] Token received, fullText length:", fullText.length);
            if (onToken) onToken(evt.token, fullText);
          } else if (evt.tool_call) {
            if (onToolCall) onToolCall(evt.tool_call);
          } else if (evt.approval_request) {
            if (onApproval) onApproval(evt.approval_request);
          } else if (evt.tool_result) {
            if (onToolResult) onToolResult(evt.tool_result);
          } else if (evt.thinking) {
            console.log("[LocalMind] Thinking:", evt.thinking);
            if (onThinking) onThinking(evt.thinking);
          } else if (evt.task_estimate) {
            console.log("[LocalMind] Task estimate:", evt.task_estimate);
            if (onEstimate) onEstimate(evt.task_estimate);
          } else if (evt.analytics) {
            if (onAnalytics) onAnalytics(evt.analytics);
          } else if (evt.title_update) {
            if (onTitleUpdate) onTitleUpdate(evt.title_update);
          } else if (evt.done) {
            if (onDone) onDone(evt);
            reader.cancel();
            break;
          } else if (evt.error) {
            if (onError) onError(evt.error);
          }
        }
      }

      // Stream completed successfully — exit the retry loop
      return fullText;
    } catch (retryErr) {
      // AbortError means the user cancelled — don't retry
      if (retryErr.name === "AbortError") throw retryErr;

      console.warn("[LocalMind] Stream error on attempt", attempt + 1, retryErr);

      if (attempt < MAX_RETRIES) {
        // Notify UI about reconnection attempt
        if (onReconnecting) onReconnecting(attempt + 2, MAX_RETRIES + 1);
        await new Promise((r) => setTimeout(r, 1000));
      } else {
        // All retries exhausted — rethrow so caller handles it
        throw retryErr;
      }
    }
  }

  return fullText;
}
