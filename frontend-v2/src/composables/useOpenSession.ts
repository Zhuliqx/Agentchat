import { useChatStore } from "@/stores/chat";
import { useSessionsStore } from "@/stores/sessions";

/**
 * 打开会话的统一入口（侧栏列表与欢迎页"最近会话"共用）：
 * 切换当前会话并加载历史；点在当前会话上直接返回，避免重载并中止在途回答。
 */
export function useOpenSession() {
  const sessions = useSessionsStore();
  const chat = useChatStore();
  return function openSession(id: string) {
    if (!id || id === sessions.currentId) return;
    sessions.currentId = id;
    chat.loadHistory(id);
  };
}
