// SSE 流式读取：解析 data: 帧并回调
import type { SSEEvent } from "@/types/api";

export async function readSSEStream(
  res: Response,
  onEvent: (ev: SSEEvent) => void | Promise<void>
): Promise<void> {
  if (!res.body) throw new Error("当前浏览器不支持流式响应");
  const reader = res.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      const ev = parseFrame(frame);
      if (ev) await onEvent(ev);
    }
  }
  if (buffer.trim()) {
    const ev = parseFrame(buffer);
    if (ev) await onEvent(ev);
  }
}

export function parseFrame(frame: string): SSEEvent | null {
  if (!frame.startsWith("data:")) return null;
  const raw = frame.slice(5).trim();
  if (!raw) return null;
  try {
    return JSON.parse(raw) as SSEEvent;
  } catch {
    return null;
  }
}
