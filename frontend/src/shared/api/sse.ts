import { API_BASE_URL } from "@/shared/api/client";
import { useAuthStore } from "@/shared/store/authStore";

// A parsed server-sent event from the streaming chat endpoint.
export interface StreamEvent {
  type: string;
  data: Record<string, unknown>;
  index?: number;
}

export interface StreamHandlers {
  onEvent: (event: StreamEvent) => void;
  onError?: (error: Error) => void;
  signal?: AbortSignal;
}

/**
 * POST a chat turn and consume the SSE token stream.
 *
 * EventSource only supports GET, and the chat endpoint is an authenticated POST,
 * so we stream the response body with fetch + a ReadableStream reader and parse
 * the `event:` / `data:` SSE frames ourselves. Cancellation is via AbortSignal.
 */
export async function streamChat(
  body: Record<string, unknown>,
  { onEvent, onError, signal }: StreamHandlers,
): Promise<void> {
  const token = useAuthStore.getState().accessToken;
  try {
    const response = await fetch(`${API_BASE_URL}/chat/messages`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify(body),
      signal,
    });

    if (!response.ok || !response.body) {
      throw new Error(`Chat request failed (${response.status})`);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let boundary = buffer.indexOf("\n\n");
      while (boundary !== -1) {
        const frame = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const parsed = parseFrame(frame);
        if (parsed) onEvent(parsed);
        boundary = buffer.indexOf("\n\n");
      }
    }
  } catch (error) {
    if ((error as Error).name === "AbortError") return;
    onError?.(error instanceof Error ? error : new Error(String(error)));
  }
}

function parseFrame(frame: string): StreamEvent | null {
  const dataLines = frame
    .split("\n")
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice(5).trim());
  if (dataLines.length === 0) return null;
  try {
    const payload = JSON.parse(dataLines.join("\n"));
    return {
      type: payload.type ?? "message",
      data: payload.data ?? {},
      index: payload.index,
    };
  } catch {
    return null;
  }
}
