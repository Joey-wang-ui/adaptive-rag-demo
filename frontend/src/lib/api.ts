import type { BusinessProfile, ChatRequest, DesktopSettings, DesktopStatus, Principal, StreamEvent } from "../types";
import { SseDecoder } from "./sse";

const API_BASE = import.meta.env.VITE_API_BASE ?? "";

export class AuthenticationError extends Error {}

async function requestJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE}/api/v1${path}`, { ...init, credentials: "same-origin" });
  if (response.status === 401) throw new AuthenticationError("登录已失效，请重新登录。");
  if (!response.ok) {
    let message = `请求失败（HTTP ${response.status}），请检查账号或服务日志。`;
    try { const body: unknown = await response.json(); if (typeof body === "object" && body !== null && "detail" in body && typeof body.detail === "string") message = body.detail; } catch { /* Keep the status-only error. */ }
    throw new Error(message);
  }
  return response.json() as Promise<T>;
}

export const getProfile = () => requestJson<BusinessProfile>("/profile");
export const getPrincipal = () => requestJson<Principal>("/auth/me");
export const login = (username: string, password: string) => requestJson<Principal>("/auth/login", {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username, password }),
});
export async function logout(): Promise<void> {
  const response = await fetch(`${API_BASE}/api/v1/auth/logout`, { method: "POST", credentials: "same-origin" });
  if (!response.ok && response.status !== 401) throw new Error("退出未完成，请重试。");
}

export async function checkHealth(signal?: AbortSignal): Promise<boolean> {
  try {
    const response = await fetch(`${API_BASE}/api/v1/health`, { signal });
    return response.ok;
  } catch {
    return false;
  }
}

export async function streamChat(
  payload: ChatRequest,
  onEvent: (event: StreamEvent) => void,
  signal: AbortSignal,
): Promise<void> {
  const response = await fetch(`${API_BASE}/api/v1/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(payload),
    credentials: "same-origin",
    signal,
  });
  if (!response.ok) {
    if (response.status === 401) throw new AuthenticationError("登录已失效，请重新登录。");
    let message = `请求失败（HTTP ${response.status}）`;
    try {
      const error: unknown = await response.json();
      if (typeof error === "object" && error !== null && "message" in error) {
        message = String(error.message);
      }
    } catch { /* Use the HTTP status when no JSON error body is available. */ }
    throw new Error(message);
  }
  if (!response.body) throw new Error("浏览器不支持流式响应");

  const reader = response.body.getReader();
  const decoder = new SseDecoder();
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      for (const event of decoder.feed(value)) onEvent(event);
    }
    for (const event of decoder.finish()) onEvent(event);
  } finally {
    reader.releaseLock();
  }
}

export async function getDesktopStatus(): Promise<DesktopStatus | null> {
  const response = await fetch(`${API_BASE}/api/v1/desktop/status`, { credentials: "same-origin" });
  if (response.status === 404) return null;
  if (!response.ok) throw new Error("无法读取桌面设置状态");
  return response.json() as Promise<DesktopStatus>;
}
export const setupDesktop = (token: string, username: string, password: string, apiKey: string, remember: boolean) => requestJson<Principal>("/desktop/setup", {
  method: "POST", headers: { "Content-Type": "application/json", "X-Jiahui-Setup": token }, body: JSON.stringify({ username, password, api_key: apiKey, remember_key: remember }),
});
export const getDesktopSettings = () => requestJson<DesktopSettings>("/desktop/settings");
export const updateDesktopModel = (key: string, remember: boolean) => requestJson<{ capabilities: BusinessProfile["capabilities"]; persisted: boolean }>("/desktop/model", {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ api_key: key, remember_key: remember }),
});
export const importDesktopFile = (kind: "products" | "pdf", filename: string, content: string, status: "draft" | "current", version: string) => requestJson<{ imported?: number; total?: number; pages?: number; message?: string }>(`/desktop/import/${kind}`, {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ filename, content, status, version }),
});
