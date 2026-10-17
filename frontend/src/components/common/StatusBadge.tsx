import {
  BadgeCheck,
  Ban,
  CircleAlert,
  Clock3,
  FileQuestion,
  LockKeyhole,
  type LucideIcon,
} from "lucide-react";

import type { AssetVisualStatus } from "../../api/contracts";

const assetIcons: Record<AssetVisualStatus, LucideIcon> = {
  candidate: Clock3,
  final: BadgeCheck,
  rejected: Ban,
  blocked: CircleAlert,
};

export const AssetStatusBadge = ({ status, label }: { status: AssetVisualStatus; label: string }) => {
  const Icon = assetIcons[status];
  return (
    <span className={`status-badge status-badge--${status}`}>
      <Icon aria-hidden="true" size={16} strokeWidth={2.25} />
      <span>{label}</span>
    </span>
  );
};

export const FutureLockBadge = ({ count }: { count: number }) => (
  <span className="status-badge status-badge--locked">
    <LockKeyhole aria-hidden="true" size={16} strokeWidth={2.25} />
    <span>{count > 0 ? `${count} 章尚未解锁` : "全部章节已解锁"}</span>
  </span>
);

export const UnknownBadge = ({ children }: { children: React.ReactNode }) => (
  <span className="status-badge status-badge--unknown">
    <FileQuestion aria-hidden="true" size={16} strokeWidth={2.25} />
    <span>{children}</span>
  </span>
);
