import { ClipboardList, Heart, Sparkles } from "lucide-react";

import type { Scene } from "../../api/contracts";
import { auditText } from "../../view-models/presentation";

interface ContinuityDrawerProps {
  scene: Scene | null;
  god: boolean;
  remainingChapters?: number;
}

const AuditList = ({ title, records, empty }: { title: string; records: Record<string, unknown>[]; empty: string }) => (
  <details className="audit-disclosure">
    <summary>{title}（{records.length}）</summary>
    {records.length ? (
      <ul className="audit-list">
        {records.map((record, index) => <li key={`${title}-${index}`}><pre>{auditText(record)}</pre></li>)}
      </ul>
    ) : <p>{empty}</p>}
  </details>
);

const asRecord = (value: unknown): Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value) ? value as Record<string, unknown> : {};

const asText = (value: unknown): string | null =>
  typeof value === "string" && value.trim() ? value.trim() : null;

const storyClues = (scene: Scene, remainingChapters: number): string[] => {
  const state = scene.state ?? {};
  const relationship = asRecord(state.relationship);
  const clues: string[] = [];
  const relationshipDescription = asText(relationship.desc);
  const mood = asText(state.mood);
  if (relationshipDescription) {
    clues.push(relationshipDescription);
  }
  if (mood) {
    clues.push(`此刻心情：${mood}`);
  }
  const plans = Array.isArray(state.today_plan) ? state.today_plan.map(asRecord) : [];
  const activePlan = plans.find((plan) => plan.status === "in_progress") ?? plans.find((plan) => plan.status === "planned");
  const activePlanText = asText(activePlan?.what);
  if (activePlanText) {
    clues.push(`正在经历：${activePlanText}`);
  }
  const promises = Array.isArray(state.promises) ? state.promises.map(asRecord) : [];
  const openPromise = promises.find((promise) => promise.status === "open");
  const promiseText = asText(openPromise?.what);
  if (promiseText) {
    clues.push(`还记得约定：${promiseText}`);
  }
  if (remainingChapters > 0) {
    clues.push(`后面还有 ${remainingChapters} 章，继续故事后会逐章揭开。`);
  } else {
    clues.push("这一段故事已经完整展开。");
  }
  return clues;
};

export const ContinuityDrawer = ({ scene, god, remainingChapters = 0 }: ContinuityDrawerProps) => (
  <section className="notebook-section continuity-drawer" aria-labelledby="continuity-heading">
    <header className="section-heading">
      <div>
        <p className="eyebrow">{god ? "连续性数据" : "此刻线索"}</p>
        <h2 id="continuity-heading">{god ? "导演审计抽屉" : "柠柠的此刻"}</h2>
      </div>
      {god ? <ClipboardList aria-hidden="true" size={21} /> : <Heart aria-hidden="true" size={21} />}
    </header>
    {!scene ? <p className="empty-state">没有已编译的场景说明。</p> : god ? (
      <>
        <AuditList title="场景数据" records={[scene.sceneSpec]} empty="尚无场景数据。" />
        <AuditList title="事实依据" records={scene.evidence} empty="没有可展示的事实依据。" />
        <AuditList title="连续性冲突" records={scene.conflicts} empty="没有登记冲突。" />
        <AuditList title="未解锁片段" records={scene.futureLocks} empty="没有未解锁记录。" />
      </>
    ) : (
      <ul className="continuity-summary">
        {storyClues(scene, remainingChapters).map((clue) => <li key={clue}><Sparkles aria-hidden="true" size={16} /><span>{clue}</span></li>)}
      </ul>
    )}
  </section>
);
