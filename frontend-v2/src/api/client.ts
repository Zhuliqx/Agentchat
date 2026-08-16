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
    if (typeof data.detail === "string") detail = data.detail;
    else if (data.detail && typeof data.detail.message === "string")
      detail = data.detail.message;
    else if (data.message) detail = data.message;
    if (data.code) code = data.code;
  } catch {
    /* ignore */
  }
  return new ApiError(res.status, detail, code);
}

export async function api<T = unknown>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const headers: Record<string, string> = {};
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  if (!(options.body instanceof FormData)) headers["Content-Type"] = "application/json";

  const res = await fetch("/api" + path, { ...options, headers });
  if (!res.ok) throw await parseError(res);
  if (res.status === 204) return null as T;
  return (await res.json()) as T;
}
