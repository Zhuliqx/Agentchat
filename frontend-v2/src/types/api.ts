// 与后端 API 对应的类型定义

export interface Session {
  id: string;
  title: string;
  pinned?: boolean;
  created_at: string;
  updated_at: string;
}

export interface Message {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  sources?: string[];
  created_at?: string;
}

export interface User {
  id: string;
  username: string;
  created_at: string;
  avatar_color?: string;
  is_admin?: boolean;
}

export interface EvalCase {
  query: string;
  keywords: string[];
  type?: string; // auto | builtin | custom
  doc?: string;
  label?: string;
  hit?: boolean;
  hits?: number;
  first?: string;
}

export interface EvalDoc {
  source: string;
  name: string;
}

export interface AuthStats {
  username: string;
  created_at: string;
  session_count: number;
  message_count: number;
  memory_count: number;
  document_count: number;
  token_estimate: number;
}

export interface Doc {
  id: string;
  filename: string;
  source: string;
  chunks: number;
  has_file: boolean;
  tag?: string | null;
}

export interface Memory {
  id: string;
  user_id: string;
  content: string;
  created_at: string;
  updated_at: string;
}

export interface SearchResult {
  sessions: { id: string; title: string; pinned?: boolean; updated_at?: string }[];
  messages: {
    session_id: string;
    session_title: string;
    message_id: string;
    role: string;
    content: string;
    created_at?: string;
  }[];
}

export interface AdminStats {
  user_count: number;
  session_count: number;
  message_count: number;
  document_count: number;
}

export interface AdminUser {
  id: string;
  username: string;
  avatar_color: string;
  created_at: string;
  is_admin: boolean;
  session_count: number;
  message_count: number;
  document_count: number;
}

export interface SessionStats {
  session_id: string;
  message_count: number;
  user_count: number;
  assistant_count: number;
  system_count: number;
  rounds: number;
  total_chars: number;
  est_tokens: number;
  avg_user_chars: number;
  avg_assistant_chars: number;
  longest_response_chars: number;
  first_at: string | null;
  last_at: string | null;
  duration_sec: number | null;
}

export interface Checkpoint {
  checkpoint_id: string;
  created_at: string;
  next: string[];
  summary: string;
  interrupted: boolean;
}

export interface Task {
  id: string;
  name: string;
  task_type: string;
  task_label: string;
  task_desc: string;
  schedule: string;
  enabled: boolean;
  created_at: string;
  last_run_at: string | null;
  last_status: string | null;
  last_error: string | null;
  next_run_at: string | null;
}

export interface TaskRegistryItem {
  type: string;
  label: string;
  desc: string;
}

export interface Health {
  status: string;
  mcp_servers: string[];
}

// ---------- SSE 事件（/api/chat/stream） ----------
export interface SSEEvent {
  type: "token" | "message" | "interrupt" | "error" | string;
  content: string;
  data?: Record<string, unknown> & {
    session_id?: string;
    message_id?: string;
    user_message_id?: string;
    sources?: string[];
  };
}

// ---------- 模型切换（/api/models） ----------
export interface ModelInfo {
  id: string;
  provider: string;
  model: string;
  label: string;
}

export interface ModelListResponse {
  models: ModelInfo[];
  current: { provider: string; model: string } | null;
}

// ---------- 自主任务 Agent（/api/agent-tasks/*） ----------
export interface AgentTaskRunBody {
  goal: string;
  session_id?: string;
  checkpoint_id?: string;
  checkpoint_ns?: string;
}

export interface AgentTaskPending {
  type?: string;
  goal?: string;
  next_action?: string;
  expected_source?: string;
  step?: number;
  findings?: string[];
}

export interface AgentTaskResult {
  session_id: string;
  status: "done" | "awaiting_confirm";
  plan?: string | null;
  findings?: string[];
  final_answer?: string;
  pending?: AgentTaskPending | null;
}

/** SSE 帧（/api/agent-tasks/run/stream）：event 过程事件 / result 结果 / error 错误 */
export interface AgentTaskFrame {
  type: "event" | "result" | "error";
  kind?: string;
  data: Record<string, unknown> & { message?: string };
}

export interface AgentTaskHistoryItem extends Checkpoint {
  checkpoint_ns?: string;
  parent_checkpoint_id?: string | null;
  task_count?: number;
}
