import type { Asset, Dashboard, ViewMode } from "../api/contracts";

export type MutationName =
  | "generate"
  | "position"
  | "review_accept"
  | "review_reject";

export interface QueuedAction {
  name: MutationName;
  label: string;
  payload: Record<string, string>;
  warning: string;
}

export interface GameState {
  phase: "loading" | "ready" | "error";
  viewMode: ViewMode;
  dashboard: Dashboard | null;
  godAssets: Asset[];
  requestId: number;
  error: string | null;
  selectedAssetId: string | null;
  atmosphereEnabled: boolean;
  chartsRequested: boolean;
  queuedAction: QueuedAction | null;
  pendingAction: MutationName | null;
  notice: string | null;
}

export type GameEvent =
  | { type: "request"; requestId: number; viewMode: ViewMode }
  | { type: "resolved"; requestId: number; dashboard: Dashboard; godAssets: Asset[] }
  | { type: "failed"; requestId: number; message: string }
  | { type: "selectAsset"; assetId: string | null }
  | { type: "setAtmosphere"; enabled: boolean }
  | { type: "requestCharts" }
  | { type: "queueAction"; action: QueuedAction }
  | { type: "clearQueuedAction" }
  | { type: "actionPending"; action: MutationName }
  | { type: "actionFinished"; notice: string }
  | { type: "actionFailed"; message: string }
  | { type: "feedbackError"; message: string }
  | { type: "clearNotice" };

export const initialGameState: GameState = {
  phase: "loading",
  viewMode: "story",
  dashboard: null,
  godAssets: [],
  requestId: 0,
  error: null,
  selectedAssetId: null,
  atmosphereEnabled: false,
  chartsRequested: false,
  queuedAction: null,
  pendingAction: null,
  notice: null,
};

export const gameReducer = (state: GameState, event: GameEvent): GameState => {
  switch (event.type) {
    case "request":
      return {
        ...state,
        // Dropping a previous God snapshot before a Story request finishes is
        // deliberate: it prevents full-flow data from staying in Story memory.
        dashboard: state.viewMode === event.viewMode ? state.dashboard : null,
        godAssets: event.viewMode === "god" ? state.godAssets : [],
        phase: "loading",
        viewMode: event.viewMode,
        requestId: event.requestId,
        error: null,
        queuedAction: null,
        pendingAction: null,
      };
    case "resolved":
      return event.requestId === state.requestId
        ? {
            ...state,
            phase: "ready",
            dashboard: event.dashboard,
            godAssets: event.dashboard.viewMode === "god" ? event.godAssets : [],
            viewMode: event.dashboard.viewMode,
            error: null,
          }
        : state;
    case "failed":
      return event.requestId === state.requestId
        ? { ...state, phase: "error", error: event.message, dashboard: null }
        : state;
    case "selectAsset":
      return { ...state, selectedAssetId: event.assetId };
    case "setAtmosphere":
      return { ...state, atmosphereEnabled: event.enabled };
    case "requestCharts":
      return { ...state, chartsRequested: true };
    case "queueAction":
      return { ...state, queuedAction: event.action, notice: null };
    case "clearQueuedAction":
      return { ...state, queuedAction: null };
    case "actionPending":
      return { ...state, pendingAction: event.action, queuedAction: null, error: null };
    case "actionFinished":
      return { ...state, pendingAction: null, notice: event.notice };
    case "actionFailed":
      return { ...state, pendingAction: null, error: event.message };
    case "feedbackError":
      return { ...state, error: event.message };
    case "clearNotice":
      return { ...state, notice: null };
    default:
      return state;
  }
};

const required = (form: Record<string, string>, key: string, label: string): string => {
  const value = form[key]?.trim();
  if (!value) {
    throw new Error(`请填写${label}。`);
  }
  return value;
};

const selectedAsset = (assets: Asset[], assetId: string): Asset => {
  const asset = assets.find((item) => item.assetId === assetId);
  if (!asset) {
    throw new Error("所选媒体已不在当前本地快照中，请刷新后重试。");
  }
  return asset;
};

const identifier = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/u;

const optionalTrigger = (form: Record<string, string>, fallback: string | null): string | null => {
  const candidate = form.trigger_id?.trim() || fallback;
  if (candidate === null || candidate === "") {
    return null;
  }
  if (!identifier.test(candidate)) {
    throw new Error("章节 ID 格式无法提交。");
  }
  return candidate;
};

export const buildMutationPayload = (
  name: MutationName,
  form: Record<string, string>,
  dashboard: Dashboard,
  selectedTriggerId: string | null,
  operationId: string,
  assets: Asset[] = dashboard.assets,
): QueuedAction => {
  const checkpointId = dashboard.controls.head?.checkpointId;
  if (!checkpointId) {
    throw new Error("当前 checkpoint 不可用。请刷新后再确认操作。");
  }
  const base: Record<string, string> = {
    expected_checkpoint_id: checkpointId,
    operation_id: operationId,
  };
  switch (name) {
    case "generate": {
      if (!selectedTriggerId) {
        throw new Error("没有可生成的当前章节。");
      }
      return {
        name,
        label: "登记候选生成",
        payload: { ...base, trigger_id: selectedTriggerId },
        warning: "此操作会调用后端已配置的生成链；候选仍需人工审核，绝不自动成为最终媒体。",
      };
    }
    case "position": {
      const triggerId = optionalTrigger(form, selectedTriggerId);
      if (!triggerId) {
        throw new Error("没有可保存的位置。");
      }
      const advancing = form.advance === "true";
      return {
        name,
        label: advancing ? "继续下一章" : "保存当前位置",
        payload: { ...base, trigger_id: triggerId, view_mode: dashboard.viewMode },
        warning: advancing
          ? "将解锁下一章并保存故事进度。"
          : "此操作会记录当前章节和视图位置，并写入 append-only 历史。",
      };
    }
    case "review_accept": {
      const assetId = required(form, "asset_id", "媒体");
      const asset = selectedAsset(assets, assetId);
      if (!asset.sha256) {
        throw new Error("此媒体没有可提交的 SHA-256，不能接受。请刷新并核对验证状态。");
      }
      return {
        name,
        label: "接受媒体",
        payload: {
          ...base,
          asset_id: assetId,
          reason: required(form, "reason", "人工理由"),
          expected_sha256: asset.sha256,
        },
        warning: `将提交 SHA-256 ${asset.sha256}。人工接受会写入 append-only 审核历史。`,
      };
    }
    case "review_reject": {
      const assetId = required(form, "asset_id", "媒体");
      const asset = selectedAsset(assets, assetId);
      return {
        name,
        label: "拒绝媒体",
        payload: {
          ...base,
          asset_id: assetId,
          reason: required(form, "reason", "人工理由"),
          ...(asset.sha256 ? { expected_sha256: asset.sha256 } : {}),
        },
        warning: "拒绝理由会保留在 append-only 审核历史中；媒体记录不会被浏览器删除。",
      };
    }
  }
};
