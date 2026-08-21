// 统一 fetch 封装：JSON、错误解析、认证头
import { getToken } from "./token";

export class ApiError extends Error {
  status: number;
  code?: string;
  constructor(status: number, message: string, code?: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

export async function parseError(res: Response): Promise<ApiError> {
  let detail = res.statusText;
  let code: string | undefined;
  try {
    const data = await res.json();
    // pydantic validator 的 detail 带 "Value error, " 前缀，剥离后展示更干净
    if (typeof data.detail === "string")
      detail = data.detail.replace(/^Value error, /, "");
    else if (data.detail && typeof data.detail.message === "string")
      detail = data.detail.message;
    else if (data.message) detail = data.message;
    if (data.code) code = data.code;
  } catch {
    /* ignore */
  }
  return new ApiError(res.status, detail, code);
}

/** Bearer 认证头（未登录/无 token 时为空对象）。 */
export function authHeaders(): Record<string, string> {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/** 原始 fetch（带 /api 前缀 + 认证头），供需要 Response 的场景（SSE/文件）复用。 */
export async function apiRaw(
  path: string,
  options: RequestInit = {}
): Promise<Response> {
  const headers: Record<string, string> = { ...authHeaders() };
  if (!(options.body instanceof FormData))
    headers["Content-Type"] = "application/json";
  return fetch("/api" + path, { ...options, headers });
}

export async function api<T = unknown>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const res = await apiRaw(path, options);
  if (!res.ok) throw await parseError(res);
  if (res.status === 204) return null as T;
  return (await res.json()) as T;
}
