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
    buffer += decoder.decode(value, { stream: !done });
    let boundary: RegExpExecArray | null;
    // SSE 允许 LF 或 CRLF；不要把 CRLF 帧合并为一个无效 JSON 载荷。
    while ((boundary = /\r?\n\r?\n/.exec(buffer)) !== null) {
      const frame = buffer.slice(0, boundary.index);
      buffer = buffer.slice(boundary.index + boundary[0].length);
      const ev = parseFrame(frame);
      if (ev) await onEvent(ev);
    }
    if (done) break;
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
