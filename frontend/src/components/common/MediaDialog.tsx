import * as Dialog from "@radix-ui/react-dialog";
import { Expand, Headphones, Image as ImageIcon, Video, X } from "lucide-react";
import { useRef } from "react";

import { safeMediaUrl, type Asset } from "../../api/contracts";
import { conciseHash, mediaLabel } from "../../view-models/presentation";

interface MediaDialogProps {
  asset: Asset | null;
  onOpenChange: (open: boolean) => void;
  onEnhance: (asset: Asset, host: HTMLDivElement) => void;
}

const MediaBody = ({ asset }: { asset: Asset }) => {
  const source = safeMediaUrl(asset);
  if (!source) {
    return <p className="empty-state">这份媒体暂时无法在浏览器中打开。</p>;
  }
  if (asset.mediaType === "image") {
    return <img className="media-dialog__image" src={source} alt="章节图片" />;
  }
  if (asset.mediaType === "audio") {
    return <audio className="media-dialog__player" controls preload="metadata" src={source}>当前浏览器无法播放此本地音频。</audio>;
  }
  if (asset.mediaType === "video") {
    return <video className="media-dialog__video" controls preload="metadata" src={source}>当前浏览器无法播放此本地视频。</video>;
  }
  return <p className="empty-state">这是一份导演台数据文件，不在故事播放器中展开。</p>;
};

const MediaIcon = ({ type }: { type: Asset["mediaType"] }) => {
  if (type === "audio") {
    return <Headphones aria-hidden="true" size={20} />;
  }
  if (type === "video") {
    return <Video aria-hidden="true" size={20} />;
  }
  return <ImageIcon aria-hidden="true" size={20} />;
};

export const MediaDialog = ({ asset, onOpenChange, onEnhance }: MediaDialogProps) => {
  const enhancementHost = useRef<HTMLDivElement>(null);
  return (
  <Dialog.Root open={asset !== null} onOpenChange={onOpenChange}>
    <Dialog.Portal>
      <Dialog.Overlay className="dialog-overlay" />
      <Dialog.Content className="dialog-card dialog-card--media">
        <div className="dialog-heading">
          <div>
            <p className="eyebrow">章节收藏</p>
            <Dialog.Title className="dialog-title"><MediaIcon type={asset?.mediaType ?? "other"} />{asset ? mediaLabel(asset.mediaType) : "媒体"}</Dialog.Title>
          </div>
          <Dialog.Close asChild>
            <button type="button" className="icon-button" aria-label="关闭媒体查看器"><X aria-hidden="true" size={20} /></button>
          </Dialog.Close>
        </div>
        {asset ? <MediaBody asset={asset} /> : null}
        {asset ? (
          <details className="media-dialog__technical">
            <summary>技术信息</summary>
            <div className="media-dialog__metadata">
              <span>资产：<code>{asset.assetId}</code></span>
              <span>验证：{asset.verification.status}</span>
              <span>hash：<code>{conciseHash(asset.sha256)}</code></span>
            </div>
          </details>
        ) : null}
        {asset && safeMediaUrl(asset) ? (
          <button type="button" className="button button--quiet" onClick={() => enhancementHost.current && onEnhance(asset, enhancementHost.current)}>
            <Expand aria-hidden="true" size={17} />沉浸查看
          </button>
        ) : null}
        <div ref={enhancementHost} className="media-enhancement" aria-live="polite" />
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>
  );
};
