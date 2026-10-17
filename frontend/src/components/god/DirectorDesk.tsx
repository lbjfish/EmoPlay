import type { Asset, Dashboard } from "../../api/contracts";
import { AuditTables } from "./AuditTables";
import { ReviewWorkbench } from "./ReviewWorkbench";
import { ContinuityDrawer } from "../story/ContinuityDrawer";

interface DirectorDeskProps {
  dashboard: Dashboard;
  assets: Asset[];
  chartsRequested: boolean;
  charts: React.ReactNode;
  onRequestCharts: () => void;
  onOpenAsset: (assetId: string) => void;
}

export const DirectorDesk = ({
  dashboard,
  assets,
  chartsRequested,
  charts,
  onRequestCharts,
  onOpenAsset,
}: DirectorDeskProps) => (
  <aside className="director-desk" aria-label="导演台">
    <section className="director-desk__intro">
      <p className="eyebrow">含剧透 · 本地评审用</p>
      <h2>导演台</h2>
      <p>该视图显示完整审核事实；退出后会清空此视图的内存快照并重新读取 Story。</p>
    </section>
    <ContinuityDrawer scene={dashboard.scene} god />
    <AuditTables dashboard={dashboard} chartsRequested={chartsRequested} onRequestCharts={onRequestCharts} charts={charts} />
    <ReviewWorkbench assets={assets} onOpenAsset={onOpenAsset} />
  </aside>
);
