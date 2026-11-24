import { ApiError, getAccessToken, refreshToken } from "./api";
import { getApiBase, IS_TAURI } from "./platform";
import type { AgentEvent } from "./types";

export interface StreamHandlers {
  onEvent: (event: AgentEvent) => void;
  onDone: () => void;
  onError: (error: unknown) => void;
}

interface Block {
  event: string;
  data: string;
}

function parseBlock(raw: string): Block | null {
  let event = "message";
  const data: string[] = [];
  for (const line of raw.split("\n")) {
    if (!line || line.startsWith(":")) continue;
    const idx = line.indexOf(":");
    const field = idx === -1 ? line : line.slice(0, idx);
    let value = idx === -1 ? "" : line.slice(idx + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") event = value;
    else if (field === "data") data.push(value);
  }
  if (!data.length && event === "message") return null;
  return { event, data: data.join("\n") };
}

/**
 * 打开会话事件流（SSE over fetch）。
 * 返回取消函数；连接结束（done 或网络错误）时停止。
 */
export function openSessionStream(
  sessionId: string,
  afterSeq: number,
  handlers: StreamHandlers,
): () => void {
  const controller = new AbortController();
  let closed = false;

  const close = () => {
    if (closed) return;
    closed = true;
    controller.abort();
  };

  const run = async (retried: boolean): Promise<void> => {
    const headers: Record<string, string> = { Accept: "text/event-stream" };
    if (IS_TAURI) headers["X-Client"] = "desktop";
    const token = getAccessToken();
    if (token) headers.Authorization = `Bearer ${token}`;

    let resp: Response;
    try {
      resp = await fetch(
        `${getApiBase()}/api/sessions/${sessionId}/stream?after_seq=${afterSeq}`,
        {
          headers,
          signal: controller.signal,
          credentials: IS_TAURI ? "omit" : "include",
        },
      );
    } catch (error) {
      if (!closed) handlers.onError(error);
      return;
    }

    if (resp.status === 401 && !retried) {
      if (await refreshToken()) {
        await run(true);
        return;
      }
    }
    if (!resp.ok || !resp.body) {
      handlers.onError(new ApiError(resp.status, "stream_failed", "无法连接事件流"));
      return;
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let done = false;

    try {
      while (!closed) {
        const chunk = await reader.read();
        if (chunk.done) break;
        buffer += decoder.decode(chunk.value, { stream: true });
        let boundary = buffer.indexOf("\n\n");
        while (boundary >= 0) {
          const raw = buffer.slice(0, boundary).replace(/\r/g, "");
          buffer = buffer.slice(boundary + 2);
          const block = parseBlock(raw);
          if (block?.event === "done") {
            done = true;
            handlers.onDone();
            break;
          }
          if (block?.event === "agent_event" && block.data) {
            try {
              handlers.onEvent(JSON.parse(block.data) as AgentEvent);
            } catch {
              /* 单个坏事件不影响后续 */
            }
          }
          boundary = buffer.indexOf("\n\n");
        }
        if (done) break;
      }
    } catch (error) {
      if (!closed) handlers.onError(error);
      return;
    } finally {
      reader.cancel().catch(() => undefined);
    }
    if (!done && !closed) handlers.onDone();
  };

  run(false).catch((error) => {
    if (!closed) handlers.onError(error);
  });

  return close;
}
