import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import type { Dashboard } from "../api/contracts";

export default function GodCharts({ dashboard }: { dashboard: Dashboard }) {
  const data = dashboard.generationStatus.providerCalls.map((call, index) => ({
    name: call.provider || `调用 ${index + 1}`,
    latency: call.latencyMs || 0,
    cost: call.costAmountMicros || 0,
  }));
  if (!data.length) {
    return <p className="chart-empty">暂无调用指标；文本表格仍是完整审核通道。</p>;
  }
  return (
    <section className="god-chart" aria-label="Provider 调用补充图表">
      <p>按需加载的补充图表。计费未知不会被图表当作零成本解释。</p>
      <div className="god-chart__canvas">
        <ResponsiveContainer width="100%" height={230}>
          <BarChart data={data} accessibilityLayer>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey="name" />
            <YAxis />
            <Tooltip />
            <Bar dataKey="latency" fill="#2E6F95" name="延迟 ms" />
            <Bar dataKey="cost" fill="#E7B93E" name="成本 micros" />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </section>
  );
}
