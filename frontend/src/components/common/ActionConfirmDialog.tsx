import * as AlertDialog from "@radix-ui/react-alert-dialog";
import { Check, X } from "lucide-react";

import type { QueuedAction } from "../../state/gameReducer";

const confirmationFields = [
  ["asset_id", "媒体"],
  ["reason", "人工理由"],
  ["trigger_id", "章节"],
] as const;

interface ActionConfirmDialogProps {
  action: QueuedAction | null;
  pending: boolean;
  onClose: () => void;
  onConfirm: () => void;
}

export const ActionConfirmDialog = ({
  action,
  pending,
  onClose,
  onConfirm,
}: ActionConfirmDialogProps) => {
  const continuingStory = action?.name === "position" && action.label === "继续下一章";
  return (
  <AlertDialog.Root open={action !== null} onOpenChange={(open) => !open && !pending && onClose()}>
    <AlertDialog.Portal>
      <AlertDialog.Overlay className="dialog-overlay" />
      <AlertDialog.Content className="dialog-card" aria-describedby="action-confirm-description">
        <p className="eyebrow">{continuingStory ? "继续故事" : "需要明确确认"}</p>
        <AlertDialog.Title>{action?.label || "确认本地操作"}</AlertDialog.Title>
        <AlertDialog.Description id="action-confirm-description" className="dialog-copy">
          {action?.warning}
        </AlertDialog.Description>
        {action?.payload.expected_sha256 ? (
          <p className="hash-note">将提交 SHA-256：<code>{action.payload.expected_sha256}</code></p>
        ) : null}
        {action ? (
          <dl className="confirmation-details">
            {confirmationFields.map(([key, label]) => action.payload[key] ? (
              <div key={key}><dt>{label}</dt><dd>{action.payload[key]}</dd></div>
            ) : null)}
          </dl>
        ) : null}
        <p className="dialog-copy">{continuingStory ? "确认后会保存本地进度并展开下一章，不会上传任何内容。" : "确认后会安全保存本地变更，并重新读取最新状态。"}</p>
        <div className="dialog-actions">
          <AlertDialog.Cancel asChild>
            <button type="button" className="button button--quiet" disabled={pending}>
              <X aria-hidden="true" size={17} />取消
            </button>
          </AlertDialog.Cancel>
          <AlertDialog.Action asChild>
            <button type="button" className="button button--primary" disabled={pending} onClick={onConfirm}>
              <Check aria-hidden="true" size={17} />{pending ? "正在保存…" : continuingStory ? "解锁下一章" : "确认并保存"}
            </button>
          </AlertDialog.Action>
        </div>
      </AlertDialog.Content>
    </AlertDialog.Portal>
  </AlertDialog.Root>
  );
};
