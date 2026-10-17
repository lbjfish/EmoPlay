export type ViewMode = "story" | "god";

export type JsonPrimitive = boolean | number | string | null;
export type JsonValue = JsonPrimitive | JsonValue[] | { [key: string]: JsonValue };

type UnknownRecord = Record<string, unknown>;

export interface TimelineEntry {
  triggerId: string;
  atUtc: string | null;
  kind: string | null;
  locked: boolean;
  intent: string | null;
  why: string | null;
  hasScript: boolean;
}

export interface Message {
  messageId: string | null;
  atUtc: string | null;
  role: string | null;
  name: string | null;
  kind: string | null;
  text: string | null;
  triggerId: string | null;
}

export interface MessageFlow {
  visible: Message[];
  trigger: Message | null;
  futureCount: number;
  futureLocked: UnknownRecord[];
  all: Message[];
}

export interface Scene {
  trigger: {
    triggerId: string | null;
    atUtc: string | null;
    kind: string | null;
    intent: string | null;
    why: string | null;
    say: string | null;
  };
  state: UnknownRecord | null;
  messageFlow: MessageFlow;
  sceneSpec: UnknownRecord;
  evidence: UnknownRecord[];
  conflicts: UnknownRecord[];
  futureLocks: UnknownRecord[];
  eligibility: {
    sceneSpecFinalEligible: boolean;
    humanReviewRequired: boolean;
  };
}

export interface Asset {
  assetId: string;
  triggerId: string | null;
  mediaType: "image" | "audio" | "video" | "other";
  role: string;
  mimeType: string | null;
  sizeBytes: number | null;
  widthPx: number | null;
  heightPx: number | null;
  durationMs: number | null;
  provenance: string | null;
  finalEligible: boolean;
  sha256: string | null;
  verification: {
    status: string;
    sizeBytes: number | null;
    sha256: string | null;
  };
  reviewAction: "accept" | "reject" | null;
  reviewReason: string | null;
  reviewMutable: boolean;
  createdAtUtc: string | null;
  previewAvailable: boolean;
  previewUrl: string | null;
}

export interface Capability {
  kind: string;
  state: string;
  provider: string | null;
  reason: string | null;
  finalEligible: boolean;
}

export interface RunRecord {
  runId: string | null;
  status: string | null;
  mode: string | null;
  safeError: string | null;
  createdAtUtc: string | null;
  finishedAtUtc: string | null;
}

export interface ProviderCall {
  callId: string | null;
  runId: string | null;
  provider: string | null;
  model: string | null;
  purpose: string | null;
  status: string | null;
  billingState: string | null;
  latencyMs: number | null;
  costAmountMicros: number | null;
  errorCode: string | null;
}

export interface DecisionRecord {
  decisionId: string | null;
  subjectKind: string | null;
  subjectId: string | null;
  action: string | null;
  reason: string | null;
  decidedBy: string | null;
}

export interface Controls {
  state: "available" | "unavailable";
  reason: string | null;
  playthroughId: string | null;
  head: {
    checkpointId: string | null;
  } | null;
  generationAvailable: boolean;
  positionAvailable: boolean;
  reviewAvailable: boolean;
}

export interface Dashboard {
  viewMode: ViewMode;
  selectedTriggerId: string | null;
  timeline: TimelineEntry[];
  scene: Scene | null;
  capabilities: Capability[];
  assets: Asset[];
  controls: Controls;
  generationStatus: {
    runs: RunRecord[];
    providerCalls: ProviderCall[];
    decisions: DecisionRecord[];
  };
  empty: boolean;
}

export type AssetVisualStatus = "candidate" | "final" | "rejected" | "blocked";

const identifier = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/u;
const mediaPath = /^\/media\/[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/u;

const isRecord = (value: unknown): value is UnknownRecord =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const asString = (value: unknown): string | null => (typeof value === "string" ? value : null);
const asBoolean = (value: unknown): boolean => value === true;
const asNumber = (value: unknown): number | null =>
  typeof value === "number" && Number.isFinite(value) ? value : null;
const asArray = (value: unknown): unknown[] => (Array.isArray(value) ? value : []);
const asRecords = (value: unknown): UnknownRecord[] => asArray(value).filter(isRecord);
const nullableIdentifier = (value: unknown): string | null => {
  const candidate = asString(value);
  return candidate !== null && identifier.test(candidate) ? candidate : null;
};

const toJsonValue = (value: unknown): JsonValue => {
  if (value === null || typeof value === "string" || typeof value === "boolean") {
    return value;
  }
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (Array.isArray(value)) {
    return value.map(toJsonValue);
  }
  if (isRecord(value)) {
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, toJsonValue(item)]));
  }
  return null;
};

const toJsonRecord = (value: unknown): UnknownRecord => {
  if (!isRecord(value)) {
    return {};
  }
  return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, toJsonValue(item)]));
};

const parseMessage = (value: unknown): Message => {
  const source = isRecord(value) ? value : {};
  return {
    messageId: nullableIdentifier(source.message_id),
    atUtc: asString(source.at_utc),
    role: asString(source.role),
    name: asString(source.name),
    kind: asString(source.kind),
    text: asString(source.text),
    triggerId: nullableIdentifier(source.trigger_id),
  };
};

const parseMessageFlow = (value: unknown, viewMode: ViewMode): MessageFlow => {
  const source = isRecord(value) ? value : {};
  const trigger = isRecord(source.trigger) ? parseMessage(source.trigger) : null;
  return {
    visible: asArray(source.visible).map(parseMessage),
    trigger,
    futureCount: Math.max(0, asNumber(source.future_count) ?? 0),
    futureLocked: asRecords(source.future_locked).map(toJsonRecord),
    // Story must not retain a complete-message response even if a malformed
    // endpoint includes it. God consumes all only after the explicit mode entry.
    all: viewMode === "god" ? asArray(source.all).map(parseMessage) : [],
  };
};

const parseScene = (value: unknown, viewMode: ViewMode): Scene | null => {
  if (!isRecord(value)) {
    return null;
  }
  const trigger = isRecord(value.trigger) ? value.trigger : {};
  const eligibility = isRecord(value.eligibility) ? value.eligibility : {};
  return {
    trigger: {
      triggerId: nullableIdentifier(trigger.trigger_id),
      atUtc: asString(trigger.at_utc),
      kind: asString(trigger.kind),
      intent: asString(trigger.intent),
      why: asString(trigger.why),
      say: asString(trigger.say),
    },
    state: isRecord(value.state) ? toJsonRecord(value.state) : null,
    messageFlow: parseMessageFlow(value.message_flow, viewMode),
    sceneSpec: toJsonRecord(value.scene_spec),
    evidence: asRecords(value.evidence).map(toJsonRecord),
    conflicts: asRecords(value.conflicts).map(toJsonRecord),
    futureLocks: asRecords(value.future_locks).map(toJsonRecord),
    eligibility: {
      sceneSpecFinalEligible: asBoolean(eligibility.scene_spec_final_eligible),
      humanReviewRequired: asBoolean(eligibility.human_review_required),
    },
  };
};

const parseAsset = (value: unknown): Asset | null => {
  if (!isRecord(value)) {
    return null;
  }
  const assetId = nullableIdentifier(value.asset_id);
  if (assetId === null) {
    return null;
  }
  const verification = isRecord(value.verification) ? value.verification : {};
  const type = asString(value.media_type);
  const reviewAction = asString(value.review_action);
  const previewUrl = asString(value.preview_url);
  return {
    assetId,
    triggerId: nullableIdentifier(value.trigger_id),
    mediaType: type === "image" || type === "audio" || type === "video" ? type : "other",
    role: asString(value.role) ?? "candidate",
    mimeType: asString(value.mime_type),
    sizeBytes: asNumber(value.size_bytes),
    widthPx: asNumber(value.width_px),
    heightPx: asNumber(value.height_px),
    durationMs: asNumber(value.duration_ms),
    provenance: asString(value.provenance),
    finalEligible: asBoolean(value.final_eligible),
    sha256: asString(value.sha256),
    verification: {
      status: asString(verification.status) ?? "unavailable",
      sizeBytes: asNumber(verification.size_bytes),
      sha256: asString(verification.sha256),
    },
    reviewAction: reviewAction === "accept" || reviewAction === "reject" ? reviewAction : null,
    reviewReason: asString(value.review_reason),
    reviewMutable: asBoolean(value.review_mutable),
    createdAtUtc: asString(value.created_at_utc),
    previewAvailable: asBoolean(value.preview_available),
    previewUrl: previewUrl !== null && mediaPath.test(previewUrl) ? previewUrl : null,
  };
};

const parseControls = (value: unknown): Controls => {
  const source = isRecord(value) ? value : {};
  const head = isRecord(source.head) ? source.head : null;
  return {
    state: source.state === "available" ? "available" : "unavailable",
    reason: asString(source.reason),
    playthroughId: nullableIdentifier(source.playthrough_id),
    head: head === null
      ? null
      : {
          checkpointId: nullableIdentifier(head.checkpoint_id),
        },
    generationAvailable: asBoolean(source.generation_available),
    positionAvailable: asBoolean(source.position_available),
    reviewAvailable: asBoolean(source.review_available),
  };
};

const parseCapabilities = (value: unknown): Capability[] => {
  if (!isRecord(value)) {
    return [];
  }
  return Object.entries(value)
    .filter((entry): entry is [string, UnknownRecord] => isRecord(entry[1]))
    .map(([kind, report]) => ({
      kind,
      state: asString(report.state) ?? "unknown",
      provider: asString(report.provider),
      reason: asString(report.reason),
      finalEligible: asBoolean(report.final_eligible),
    }));
};

const parseGenerationStatus = (value: unknown): Dashboard["generationStatus"] => {
  const source = isRecord(value) ? value : {};
  return {
    runs: asRecords(source.runs).map((item) => ({
      runId: nullableIdentifier(item.run_id),
      status: asString(item.status),
      mode: asString(item.mode),
      safeError: asString(item.safe_error),
      createdAtUtc: asString(item.created_at_utc),
      finishedAtUtc: asString(item.finished_at_utc),
    })),
    providerCalls: asRecords(source.provider_calls).map((item) => ({
      callId: nullableIdentifier(item.call_id),
      runId: nullableIdentifier(item.run_id),
      provider: asString(item.provider),
      model: asString(item.model),
      purpose: asString(item.purpose),
      status: asString(item.status),
      billingState: asString(item.billing_state),
      latencyMs: asNumber(item.latency_ms),
      costAmountMicros: asNumber(item.cost_amount_micros),
      errorCode: asString(item.error_code),
    })),
    decisions: asRecords(source.decisions).map((item) => ({
      decisionId: nullableIdentifier(item.decision_id),
      subjectKind: asString(item.subject_kind),
      subjectId: nullableIdentifier(item.subject_id),
      action: asString(item.action),
      reason: asString(item.reason),
      decidedBy: asString(item.decided_by),
    })),
  };
};

export const parseAssets = (value: unknown): Asset[] =>
  asArray(value)
    .map(parseAsset)
    .filter((asset): asset is Asset => asset !== null);

export const parseDashboard = (value: unknown): Dashboard => {
  if (!isRecord(value)) {
    throw new Error("本地服务返回的资料格式无法读取。");
  }
  const viewMode: ViewMode = value.view_mode === "god" ? "god" : "story";
  const assets = parseAssets(value.assets);
  return {
    viewMode,
    selectedTriggerId: nullableIdentifier(value.selected_trigger_id),
    timeline: asRecords(value.timeline).map((item) => ({
      triggerId: nullableIdentifier(item.trigger_id) ?? "",
      atUtc: asString(item.at_utc),
      kind: asString(item.kind),
      locked: asBoolean(item.locked),
      intent: asString(item.intent),
      why: asString(item.why),
      hasScript: asBoolean(item.has_script),
    })).filter((item) => item.triggerId !== ""),
    scene: parseScene(value.scene, viewMode),
    capabilities: parseCapabilities(value.capabilities),
    assets,
    controls: parseControls(value.controls),
    generationStatus: parseGenerationStatus(value.generation_status),
    empty: asBoolean(value.empty),
  };
};

export const safeMediaUrl = (asset: Asset): string | null =>
  asset.previewAvailable && asset.previewUrl !== null && mediaPath.test(asset.previewUrl)
    ? asset.previewUrl
    : null;

export const assetVisualStatus = (asset: Asset): AssetVisualStatus => {
  if (asset.reviewAction === "reject") {
    return "rejected";
  }
  if (
    asset.reviewAction === "accept" &&
    asset.finalEligible &&
    asset.verification.status === "ok" &&
    asset.sha256 !== null
  ) {
    return "final";
  }
  if (asset.verification.status !== "ok" || !asset.previewAvailable) {
    return "blocked";
  }
  return "candidate";
};

export const isSafeIdentifier = (value: string | null): value is string =>
  value !== null && identifier.test(value);
