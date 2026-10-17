import { BarChart3 } from "lucide-react";

import type { Dashboard } from "../../api/contracts";
import { formatUtc } from "../../view-models/presentation";

interface AuditTablesProps {
  dashboard: Dashboard;
  chartsRequested: boolean;
  onRequestCharts: () => void;
  charts: React.ReactNode;
}

const EmptyRow = ({ colSpan, children }: { colSpan: number; children: React.ReactNode }) => (
  <tr><td colSpan={colSpan}>{children}</td></tr>
);

export const AuditTables = ({ dashboard, chartsRequested, onRequestCharts, charts }: AuditTablesProps) => (
  <section className="notebook-section audit-tables" aria-labelledby="audit-heading">
    <header className="section-heading">
      <div>
        <p className="eyebrow">调用与人工决定</p>
        <h2 id="audit-heading">Run / call / decision</h2>
      </div>
      <button type="button" className="button button--quiet" disabled={chartsRequested} onClick={onRequestCharts}>
        <BarChart3 aria-hidden="true" size={17} />{chartsRequested ? "指标已请求" : "按需加载指标"}
      </button>
    </header>
    <p className="help-text">表格是审核主通道；图表只在明确请求后作为补充加载。</p>
    {charts}
    <div className="table-scroll">
      <table>
        <caption>运行记录</caption>
        <thead><tr><th>Run</th><th>状态</th><th>模式</th><th>创建时间</th><th>安全错误</th></tr></thead>
        <tbody>
          {dashboard.generationStatus.runs.length ? dashboard.generationStatus.runs.map((run, index) => (
            <tr key={run.runId || `run-${index}`}><td>{run.runId || "未提供"}</td><td>{run.status || "unknown"}</td><td>{run.mode || "unknown"}</td><td>{formatUtc(run.createdAtUtc)}</td><td>{run.safeError || "—"}</td></tr>
          )) : <EmptyRow colSpan={5}>当前章节没有运行记录。</EmptyRow>}
        </tbody>
      </table>
    </div>
    <div className="table-scroll">
      <table>
        <caption>Provider 调用</caption>
        <thead><tr><th>Provider / model</th><th>目的</th><th>状态</th><th>计费</th><th>延迟</th></tr></thead>
        <tbody>
          {dashboard.generationStatus.providerCalls.length ? dashboard.generationStatus.providerCalls.map((call, index) => (
            <tr key={call.callId || `call-${index}`}><td>{call.provider || "unknown"} / {call.model || "unknown"}</td><td>{call.purpose || "—"}</td><td>{call.status || "unknown"}</td><td>{call.billingState || "unknown"}</td><td>{call.latencyMs === null ? "—" : `${call.latencyMs} ms`}</td></tr>
          )) : <EmptyRow colSpan={5}>当前章节没有 provider 调用记录。</EmptyRow>}
        </tbody>
      </table>
    </div>
    <div className="table-scroll">
      <table>
        <caption>决定记录</caption>
        <thead><tr><th>动作</th><th>对象</th><th>决定者</th><th>理由</th></tr></thead>
        <tbody>
          {dashboard.generationStatus.decisions.length ? dashboard.generationStatus.decisions.map((decision, index) => (
            <tr key={decision.decisionId || `decision-${index}`}><td>{decision.action || "unknown"}</td><td>{decision.subjectKind || "对象"} · {decision.subjectId || "—"}</td><td>{decision.decidedBy || "unknown"}</td><td>{decision.reason || "—"}</td></tr>
          )) : <EmptyRow colSpan={4}>当前章节没有人工或规则决定记录。</EmptyRow>}
        </tbody>
      </table>
    </div>
  </section>
);
