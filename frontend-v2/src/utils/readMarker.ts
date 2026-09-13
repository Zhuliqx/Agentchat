/**
 * 会话已读位置：按会话记录"最后一条已看到的message id"。
 *
 * 用 message id 而不是条数：消息被删除/重发后计数会错位，id 不会。
 * 下次打开该会话时用它算出"以下为新消息"分隔线的位置。
 */

const key = (sessionId: string) => `chat-read-${sessionId}`;

export function getReadMarker(sessionId: string): string {
  if (!sessionId) return "";
  try {
    return localStorage.getItem(key(sessionId)) || "";
  } catch {
    return "";
  }
}

export function setReadMarker(sessionId: string, messageId: string): void {
  if (!sessionId || !messageId) return;
  try {
    localStorage.setItem(key(sessionId), messageId);
  } catch {
    /* 隐私模式等场景写不了，忽略 */
  }
}
