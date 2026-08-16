// 与后端 API 对应的类型定义

export interface Session {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface Message {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  created_at?: string;
}

export interface User {
  id: string;
  username: string;
  created_at: string;
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
}

export interface Memory {
  id: string;
  user_id: string;
  content: string;
  created_at: string;
  updated_at: string;
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
  data?: Record<string, unknown> & { session_id?: string };
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
