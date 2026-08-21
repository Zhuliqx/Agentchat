// 各业务域 API（类型化）
import { api, apiRaw, ApiError, parseError } from "./client";
import { readSSEStream } from "@/utils/sse";
import type {
  AdminStats,
  AdminUser,
  AuthStats,
  Checkpoint,
  Doc,
  EvalCase,
  EvalDoc,
  Health,
  Memory,
  Message,
  ModelListResponse,
  SearchResult,
  Session,
  SessionStats,
  SSEEvent,
  Task,
  TaskRegistryItem,
  User,
} from "@/types/api";

// ---------- 健康检查 ----------
export const healthApi = {
  get: () => api<Health>("/health"),
};

// ---------- 认证 ----------
export const authApi = {
  register: (username: string, password: string) =>
    api<User>("/auth/register", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),
  login: (username: string, password: string) =>
    api<{ token: string; user: User }>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),
  me: () => api<User>("/auth/me"),
  stats: () => api<AuthStats>("/auth/stats"),
  updateProfile: (body: { username?: string; avatar_color?: string }) =>
    api<User>("/auth/me", {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  changePassword: (oldPassword: string, newPassword: string) =>
    api<User>("/auth/password", {
      method: "PUT",
      body: JSON.stringify({ old_password: oldPassword, new_password: newPassword }),
    }),
  exportData: () => api<Record<string, unknown>>("/auth/export"),
  deleteAccount: () => api<void>("/auth/me", { method: "DELETE" }),
};

// ---------- 会话 ----------
export const sessionsApi = {
  list: () => api<Session[]>("/sessions"),
  create: () =>
    api<Session>("/sessions", { method: "POST" }),
  history: (id: string) => api<Message[]>(`/sessions/${id}`),
  rename: (id: string, title: string) =>
    api<Session>(`/sessions/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ title }),
    }),
  pin: (id: string, pinned: boolean) =>
    api<Session>(`/sessions/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ pinned }),
    }),
  remove: (id: string) =>
    api<void>(`/sessions/${id}`, { method: "DELETE" }),
  batchDelete: (ids: string[]) =>
    api<{ deleted: number; requested: number }>("/sessions/batch-delete", {
      method: "POST",
      body: JSON.stringify({ ids }),
    }),
  stats: (id: string) => api<SessionStats>(`/sessions/${id}/stats`),
  deleteMessage: (sessionId: string, messageId: string) =>
    api<void>(`/sessions/${sessionId}/messages/${messageId}`, { method: "DELETE" }),
  checkpoints: (id: string) => api<Checkpoint[]>(`/sessions/${id}/checkpoints`),
  exportMarkdown: (id: string) =>
    api<{ session_id: string; markdown: string }>(`/sessions/${id}/export`),
};

// ---------- 文档 / RAG ----------
export const docsApi = {
  list: () => api<Doc[]>("/rag/documents"),
  search: (query: string, topK = 4) =>
    api<{ query: string; hits: { text: string; source: string }[] }>(
      `/rag/search?query=${encodeURIComponent(query)}&top_k=${topK}`,
      { method: "POST" }
    ),
  upload: (files: File[]) => {
    const form = new FormData();
    files.forEach((f) => form.append("file", f));
    return api<{
      tasks: { task_id: string; filename: string; file_path: string }[];
    }>("/rag/upload", {
      method: "POST",
      body: form,
    });
  },
  ingestStatus: (taskId: string) =>
    api<{
      status: "pending" | "processing" | "done" | "error";
      progress: number;
      stage: string;
      filename: string;
      result?: { chunks: number } | null;
      error?: string;
    }>(`/rag/ingest/${taskId}`),
  remove: (source: string) =>
    api<void>(`/rag/documents?source=${encodeURIComponent(source)}`, {
      method: "DELETE",
    }),
  batchRemove: (sources: string[]) =>
    api<{ deleted: number; items: { source: string; deleted_chunks: number }[] }>(
      "/rag/documents/batch-delete",
      { method: "POST", body: JSON.stringify({ sources }) }
    ),
  setTag: (source: string, tag: string | null) =>
    api<{ source: string; tag: string | null }>("/rag/documents/tag", {
      method: "PATCH",
      body: JSON.stringify({ source, tag }),
    }),
  fileUrl: (source: string, download = false) =>
    `/api/rag/documents/file?source=${encodeURIComponent(source)}${download ? "&download=1" : ""}`,
  preview: async (source: string): Promise<{ text: string; binary: boolean }> => {
    const res = await apiRaw(
      `/rag/documents/file?source=${encodeURIComponent(source)}`
    );
    if (!res.ok) throw await parseError(res);
    const ct = res.headers.get("content-type") || "";
    if (!ct.includes("text")) return { text: "", binary: true };
    return { text: await res.text(), binary: false };
  },
};

// ---------- 长期记忆 ----------
export const memoryApi = {
  list: (query = "") =>
    api<Memory[]>(
      query ? `/memory?query=${encodeURIComponent(query)}` : "/memory"
    ),
  add: (content: string) =>
    api<Memory>("/memory", {
      method: "POST",
      body: JSON.stringify({ content }),
    }),
  remove: (id: string) => api<void>(`/memory/${id}`, { method: "DELETE" }),
};

// ---------- 全局搜索 ----------
export const searchApi = {
  search: (q: string) => api<SearchResult>(`/search?q=${encodeURIComponent(q)}`),
};

// ---------- 管理员后台 ----------
export interface AdminSettingItem {
  key: string;
  type: string;
  label: string;
  value: string | number | boolean;
}
export const adminApi = {
  stats: () => api<AdminStats>("/admin/stats"),
  usage: () =>
    api<{
      items: { date: string; messages: number; tokens: number }[];
      total_messages: number;
      total_tokens: number;
    }>("/admin/usage"),
  users: () => api<AdminUser[]>("/admin/users"),
  deleteUser: (userId: string) =>
    api<void>(`/admin/users/${userId}`, { method: "DELETE" }),
  settings: () => api<{ items: AdminSettingItem[] }>("/admin/settings"),
  saveSettings: (values: Record<string, string | number | boolean>) =>
    api<{ items: AdminSettingItem[] }>("/admin/settings", {
      method: "PUT",
      body: JSON.stringify({ values }),
    }),
  eval: () =>
    api<{ docs: EvalDoc[]; builtin: EvalCase[]; auto: EvalCase[]; custom: EvalCase[] }>(
      "/admin/eval"
    ),
  saveEvalCases: (cases: { query: string; keywords: string[] }[]) =>
    api<{ custom: EvalCase[] }>("/admin/eval/custom", {
      method: "PUT",
      body: JSON.stringify({ cases }),
    }),
  runEval: (body: {
    include_auto?: boolean;
    include_builtin?: boolean;
    custom_only?: boolean;
  }) =>
    api<{
      results: EvalCase[];
      hit: number;
      total: number;
      hit_rate: number | null;
    }>("/admin/eval/run", {
      method: "POST",
      body: JSON.stringify(body),
    }),
};

// ---------- 模型切换 ----------
export const modelsApi = {
  list: () => api<ModelListResponse>("/models"),
  setCurrent: (modelId: string) =>
    api<ModelListResponse>("/models/current", {
      method: "PUT",
      body: JSON.stringify({ model_id: modelId }),
    }),
};

// ---------- 定时任务 ----------
export const tasksApi = {
  registry: () => api<TaskRegistryItem[]>("/tasks/registry"),
  list: () => api<Task[]>("/tasks"),
  create: (name: string, taskType: string, schedule: string) =>
    api<Task>("/tasks", {
      method: "POST",
      body: JSON.stringify({ name, task_type: taskType, schedule }),
    }),
  update: (id: string, patch: { name?: string; schedule?: string; enabled?: boolean }) =>
    api<Task>(`/tasks/${id}`, { method: "PATCH", body: JSON.stringify(patch) }),
  remove: (id: string) => api<void>(`/tasks/${id}`, { method: "DELETE" }),
  run: (id: string) => api<Task>(`/tasks/${id}/run`, { method: "POST" }),
};

// ---------- 聊天（SSE 流式） ----------
export interface ChatPayload {
  session_id?: string;
  message: string;
  use_rag: boolean;
  use_search: boolean;
  resume?: "confirmed" | "cancelled";
  checkpoint_id?: string;
}

export async function streamChat(
  payload: ChatPayload,
  onEvent: (ev: SSEEvent) => void | Promise<void>,
  signal?: AbortSignal
): Promise<void> {
  const res = await apiRaw("/chat/stream", {
    method: "POST",
    body: JSON.stringify(payload),
    signal,
  });
  if (!res.ok) throw await parseError(res);
  await readSSEStream(res, onEvent);
}

export { ApiError };