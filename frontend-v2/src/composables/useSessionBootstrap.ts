import { useChatStore } from "@/stores/chat";
import { useSessionsStore } from "@/stores/sessions";

/**
 * 首屏与"加载失败重试"共用：没有当前会话时选中最近一条并加载历史，
 * 一条都没有则新建空会话。已有当前会话时直接返回，避免打断进行中的回答。
 */
export async function bootstrapActiveSession(): Promise<void> {
  const sessions = useSessionsStore();
  if (sessions.currentId) return;
  const chat = useChatStore();
  if (sessions.list.length) {
    sessions.currentId = sessions.list[0].id;
    await chat.loadHistory(sessions.currentId);
    return;
  }
  await sessions.create();
  chat.clear();
}
