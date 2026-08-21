// Agent 元数据统一管理：消息轨道（chat.ts）与时间旅行（TimeTravelModal）共用，
// 避免两处各自维护 name → 标签的映射漂移。
export interface AgentMeta {
  label: string;
  emoji?: string;
  icon?: string;
}

export const AGENT_META: Record<string, AgentMeta> = {
  rag_agent: { label: "知识库", emoji: "📚", icon: "doc" },
  mcp_agent: { label: "数据库/工具", emoji: "🗄", icon: "db" },
  web_search: { label: "联网搜索", emoji: "🌐", icon: "globe" },
  search_agent: { label: "联网搜索", emoji: "🌐", icon: "globe" }, // 兼容旧事件（改名前的历史记录）
  recall_memory: { label: "记忆", emoji: "🧠", icon: "brain" },
  remember_memory: { label: "记忆", emoji: "🧠", icon: "brain" },
  request_confirmation: { label: "人工确认", emoji: "⚠️", icon: "warn" },
};
