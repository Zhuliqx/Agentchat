// 统一 fetch 封装：JSON、错误解析、认证头、401 自动刷新
import {
  clearAuth,
  getRefreshToken,
  getToken,
  setRefreshToken,
  setStoredUser,
  setToken,
} from "./token";

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
    if (typeof data.detail === "string") detail = data.detail.replace(/^Value error, /, "");
    else if (data.detail && typeof data.detail.message === "string") detail = data.detail.message;
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

let refreshPromise: Promise<boolean> | null = null;

async function doRefresh(): Promise<boolean> {
  const refreshToken = getRefreshToken();
  if (!refreshToken) return false;
  try {
    const res = await fetch("/api/auth/refresh", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refreshToken }),
    });
    if (!res.ok) return false;
    const data = await res.json();
    if (data.token && data.refresh_token) {
      setToken(data.token);
      setRefreshToken(data.refresh_token);
      if (data.user) setStoredUser(data.user);
      return true;
    }
    return false;
  } catch {
    return false;
  }
}

function refreshOnce(): Promise<boolean> {
  if (!refreshPromise) {
    refreshPromise = doRefresh().finally(() => {
      refreshPromise = null;
    });
  }
  return refreshPromise;
}

function shouldRefresh(path: string): boolean {
  return (
    !path.startsWith("/auth/login") &&
    !path.startsWith("/auth/register") &&
    !path.startsWith("/auth/refresh") &&
    !path.startsWith("/auth/logout")
  );
}

/** 原始 fetch（带 /api 前缀 + 认证头），供需要 Response 的场景（SSE/文件）复用。 */
export async function apiRaw(path: string, options: RequestInit = {}): Promise<Response> {
  const headers: Record<string, string> = { ...authHeaders() };
  if (options.headers) {
    for (const [key, value] of new Headers(options.headers).entries()) {
      for (const existing of Object.keys(headers)) {
        if (existing.toLowerCase() === key.toLowerCase()) delete headers[existing];
      }
      headers[key] = value;
    }
  }
  const hasContentType = Object.keys(headers).some((key) => key.toLowerCase() === "content-type");
  if (!(options.body instanceof FormData) && !hasContentType)
    headers["Content-Type"] = "application/json";
  const attempt = (h: Record<string, string>) => fetch("/api" + path, { ...options, headers: h });

  let res = await attempt(headers);
  if (
    res.status === 401 &&
    getToken() &&
    shouldRefresh(path) &&
    getRefreshToken() &&
    (await refreshOnce())
  ) {
    // access 过期 → refresh 成功 → 用新 access 重放一次原请求
    res = await attempt({ ...headers, ...authHeaders() });
  }
  if (res.status === 401 && getToken() && shouldRefresh(path)) {
    clearAuth();
    window.dispatchEvent(new Event("auth-expired"));
  }
  return res;
}

export async function api<T = unknown>(path: string, options: RequestInit = {}): Promise<T> {
  const res = await apiRaw(path, options);
  if (!res.ok) throw await parseError(res);
  if (res.status === 204) return null as T;
  return (await res.json()) as T;
}
