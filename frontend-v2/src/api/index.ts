// 各业务域 API（类型化）
import { api, ApiError, parseError } from "./client";
import { getToken } from "./token";
import { readSSEStream } from "@/utils/sse";
import type {
  AuthStats,
  Checkpoint,
  Doc,
  Health,
  Memory,
  Message,
  ModelListResponse,
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
  remove: (id: string) =>
    api<void>(`/sessions/${id}`, { method: "DELETE" }),
  batchDelete: (ids: string[]) =>
    api<{ deleted: number; requested: number }>("/sessions/batch-delete", {
      method: "POST",
      body: JSON.stringify({ ids }),
    }),
  stats: (id: string) => api<SessionStats>(`/sessions/${id}/stats`),
  checkpoints: (id: string) => api<Checkpoint[]>(`/sessions/${id}/checkpoints`),
  exportMarkdown: (id: string) =>
    api<{ session_id: string; markdown: string }>(`/sessions/${id}/export`),
};

// ---------- 文档 / RAG ----------
export const docsApi = {
  list: () => api<Doc[]>("/rag/documents"),
  upload: (files: File[]) => {
    const form = new FormData();
    files.forEach((f) => form.append("file", f));
    return api<{ filename: string; chunks: number }>("/rag/upload", {
      method: "POST",
      body: form,
    });
  },
  remove: (source: string) =>
    api<void>(`/rag/documents?source=${encodeURIComponent(source)}`, {
      method: "DELETE",
    }),
  fileUrl: (source: string, download = false) =>
    `/api/rag/documents/file?source=${encodeURIComponent(source)}${download ? "&download=1" : ""}`,
  preview: async (source: string): Promise<{ text: string; binary: boolean }> => {
    const res = await fetch(
      `/api/rag/documents/file?source=${encodeURIComponent(source)}`,
      { headers: getToken() ? { Authorization: `Bearer ${getToken()}` } : {} }
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
  const res = await fetch("/api/chat/stream", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(getToken() ? { Authorization: `Bearer ${getToken()}` } : {}),
    },
    body: JSON.stringify(payload),
    signal,
  });
  if (!res.ok) throw await parseError(res);
  await readSSEStream(res, onEvent);
}

export { ApiError };
