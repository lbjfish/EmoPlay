import { parseAssets, parseDashboard, type Asset, type Dashboard, type ViewMode } from "./contracts";

export class LocalApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "LocalApiError";
    this.status = status;
  }
}

const tokenSelector = 'meta[name="nining-action-token"]';
const safeMessage = (value: unknown): string => {
  if (typeof value === "string" && value.trim()) {
    return value.trim().slice(0, 320);
  }
  return "本地请求未能完成。";
};

const errorMessage = (payload: unknown): string => {
  if (
    typeof payload === "object" &&
    payload !== null &&
    "error" in payload &&
    typeof payload.error === "object" &&
    payload.error !== null &&
    "message" in payload.error
  ) {
    return safeMessage(payload.error.message);
  }
  return "本地请求未能完成。";
};

const readJson = async (response: Response): Promise<unknown> => {
  try {
    return await response.json();
  } catch {
    throw new LocalApiError("本地服务返回了无法读取的响应。", response.status);
  }
};

const request = async (path: string, init: RequestInit = {}): Promise<unknown> => {
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  const response = await fetch(path, {
    credentials: "same-origin",
    headers,
    ...init,
  });
  const payload = await readJson(response);
  if (!response.ok) {
    throw new LocalApiError(errorMessage(payload), response.status);
  }
  return payload;
};

export const actionToken = (): string => {
  const actionCapability = document.querySelector<HTMLMetaElement>(tokenSelector)?.content ?? "";
  if (!actionCapability) {
    throw new LocalApiError("本地操作令牌已失效，请刷新页面。", 403);
  }
  return actionCapability;
};

export const operationId = (): string => {
  if (globalThis.crypto?.randomUUID) {
    return globalThis.crypto.randomUUID();
  }
  return `op-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
};

export const loadDashboard = async (
  viewMode: ViewMode,
  triggerId: string | null,
  signal?: AbortSignal,
): Promise<Dashboard> => {
  const query = new URLSearchParams({ view: viewMode });
  if (triggerId !== null) {
    query.set("trigger", triggerId);
  }
  const payload = await request(`/api/v1/dashboard?${query.toString()}`, { signal });
  return parseDashboard(payload);
};

export const loadAllAssets = async (signal?: AbortSignal): Promise<Asset[]> => {
  const payload = await request("/api/v1/assets", { signal });
  if (typeof payload !== "object" || payload === null || !("assets" in payload)) {
    throw new LocalApiError("本地媒体清单格式无法读取。", 200);
  }
  return parseAssets(payload.assets);
};

export const submitAction = async (
  action: string,
  payload: Record<string, unknown>,
): Promise<void> => {
  await request(`/api/v1/actions/${encodeURIComponent(action)}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Nining-Action-Token": actionToken(),
    },
    body: JSON.stringify(payload),
  });
};
