import { assetVisualStatus, type Asset, type AssetVisualStatus, type JsonValue } from "../api/contracts";

const sensitiveKey = /(authorization|cookie|endpoint|key|prompt|secret|token)/iu;

export const formatUtc = (value: string | null): string => {
  if (!value) {
    return "时间未提供";
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    timeZoneName: "short",
  }).format(parsed);
};

export const conciseHash = (value: string | null): string =>
  value && value.length > 14 ? `${value.slice(0, 10)}…${value.slice(-4)}` : value ?? "未提供";

export const mediaLabel = (type: Asset["mediaType"]): string => {
  switch (type) {
    case "image":
      return "图片";
    case "audio":
      return "音频";
    case "video":
      return "视频";
    default:
      return "媒体";
  }
};

export const assetStatusCopy = (asset: Asset): { status: AssetVisualStatus; label: string; detail: string } => {
  switch (assetVisualStatus(asset)) {
    case "final":
      return {
        status: "final",
        label: "Final · 已接受",
        detail: "人类已接受，hash 已复核。",
      };
    case "rejected":
      return {
        status: "rejected",
        label: "Rejected · 已拒绝",
        detail: asset.reviewReason || "已记录人工或规则拒绝理由。",
      };
    case "blocked":
      return {
        status: "blocked",
        label: "Blocked · 阻断",
        detail: `验证状态：${asset.verification.status}。停止自动重试，需人工核对。`,
      };
    default:
      return {
        status: "candidate",
        label: "Candidate · 候选",
        detail: "已登记，仍需人工审阅；预览或生成成功不等于最终媒体。",
      };
  }
};

const safeValue = (value: JsonValue, depth = 0): JsonValue => {
  if (depth >= 4) {
    return "…";
  }
  if (Array.isArray(value)) {
    return value.slice(0, 16).map((item) => safeValue(item, depth + 1));
  }
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value)
        .filter(([key]) => !sensitiveKey.test(key))
        .map(([key, item]) => [key, safeValue(item, depth + 1)]),
    );
  }
  return value;
};

export const auditText = (value: Record<string, unknown>): string => {
  const sanitized = safeValue(value as { [key: string]: JsonValue });
  return JSON.stringify(sanitized, null, 2);
};

export const pluralLock = (count: number): string =>
  count > 0 ? `有 ${count} 条后续内容保持锁定` : "没有后续内容锁定";
