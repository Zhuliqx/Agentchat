/* 前端核心模块：DOM 助手、全局状态、工具函数（ES Module 各文件共享）。 */
"use strict";

export const API = "/api";
export const $ = (sel) => document.querySelector(sel);

// ---------------- 认证（JWT） ----------------
const TOKEN_KEY = "agentchat_token";
const USER_KEY = "agentchat_user";

export function getToken() {
  return localStorage.getItem(TOKEN_KEY) || "";
}
export function setAuth(token, user) {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
  if (user) localStorage.setItem(USER_KEY, JSON.stringify(user));
  else localStorage.removeItem(USER_KEY);
}
export function getStoredUser() {
  try {
    return JSON.parse(localStorage.getItem(USER_KEY) || "null");
  } catch (_) {
    return null;
  }
}
export function isLoggedIn() {
  return Boolean(getToken());
}
/** 附加 Authorization 头（登录后自动带上）。 */
export function authHeaders(extra = {}) {
  const token = getToken();
  if (!token) return extra;
  return { Authorization: `Bearer ${token}`, ...extra };
}

export const state = {
  sessions: [],
  currentSessionId: null,
  documents: [],
  memories: [],
  sending: false,
  lastAttempt: null, // SSE 失败时保留待重试的消息
  abortController: null, // 流式生成中可中断
  batchMode: false, // 会话批量管理模式
  selectedSessions: new Set(), // 批量模式下勾选的会话 id
};

/** 解析 fetch 非 2xx 响应的错误信息（兼容 detail 字符串 / 结构化 / message）。 */
export async function parseError(res) {
  let detail = res.statusText;
  try {
    const data = await res.json();
    if (typeof data.detail === "string") detail = data.detail;
    else if (data.detail && typeof data.detail.message === "string") detail = data.detail.message;
    else if (data.message) detail = data.message;
  } catch (_) {}
  return new Error(detail);
}

export async function api(path, options = {}) {
  const headers = authHeaders(
    options.body instanceof FormData ? {} : { "Content-Type": "application/json" }
  );
  const res = await fetch(API + path, { ...options, headers });
  if (!res.ok) throw await parseError(res);
  if (res.status === 204) return null;
  return res.json();
}

export function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

/** 渲染 Markdown 为安全 HTML（marked 解析 + DOMPurify 消毒）。 */
export function renderMarkdown(src) {
  const s = String(src || "");
  if (window.marked) {
    const raw = marked.parse(s, { breaks: true, gfm: true });
    return window.DOMPurify ? DOMPurify.sanitize(raw) : raw;
  }
  return escapeHtml(s).replace(/\n/g, "<br>");
}

/** Agent 用途映射（单一来源）：轨道节点与 Time Travel badge 共用。 */
export const AGENT_META = {
  rag_agent: ["📚", "知识库"],
  mcp_agent: ["🗄", "数据库/工具"],
  search_agent: ["🌐", "联网搜索"],
  recall_memory: ["🧠", "记忆"],
  remember_memory: ["🧠", "记忆"],
  request_confirmation: ["⚠️", "人工确认"],
};

// 由 AGENT_META 键生成，避免名单与映射表两处维护
export const AGENT_NAME_RE = new RegExp(Object.keys(AGENT_META).join("|"));
