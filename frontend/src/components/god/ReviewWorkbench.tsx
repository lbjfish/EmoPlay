import { FileCheck2, Headphones, Image as ImageIcon, Video } from "lucide-react";

import { safeMediaUrl, type Asset } from "../../api/contracts";
import { assetStatusCopy, conciseHash, mediaLabel } from "../../view-models/presentation";
import { AssetStatusBadge } from "../common/StatusBadge";

interface ReviewWorkbenchProps {
  assets: Asset[];
  onOpenAsset: (assetId: string) => void;
}

export const ReviewWorkbench = ({ assets, onOpenAsset }: ReviewWorkbenchProps) => {
  const mediaAssets = assets.filter((asset) => asset.mediaType !== "other");
  const dataFileCount = assets.length - mediaAssets.length;
  return (
    <section className="notebook-section review-workbench" aria-labelledby="review-heading">
      <header className="section-heading">
        <div>
          <p className="eyebrow">媒体总览</p>
          <h2 id="review-heading">导演媒体工作台</h2>
        </div>
        <FileCheck2 aria-hidden="true" size={21} />
      </header>
      <p className="help-text">查看各章节媒体的验证状态、hash、来源和预览。</p>
      {dataFileCount > 0 ? <p className="help-text">{dataFileCount} 份场景数据已归入上方“连续性数据”，不与图片和音频混排。</p> : null}
      {mediaAssets.length ? <ul className="review-assets">{mediaAssets.map((asset) => {
        const status = assetStatusCopy(asset);
        const source = safeMediaUrl(asset);
        return (
          <li key={asset.assetId} className="director-asset-card">
            <div className={`director-asset-card__preview director-asset-card__preview--${asset.mediaType}`}>
              {asset.mediaType === "image" && source ? <button type="button" onClick={() => onOpenAsset(asset.assetId)} aria-label={`查看 ${asset.triggerId ?? "参考"} 图片`}><img src={source} alt="" loading="lazy" /></button> : null}
              {asset.mediaType === "audio" && source ? <><Headphones aria-hidden="true" size={22} /><audio controls preload="metadata" src={source}>无法播放音频。</audio></> : null}
              {asset.mediaType === "video" && source ? <video controls preload="metadata" src={source}>无法播放视频。</video> : null}
              {asset.mediaType === "image" && !source ? <ImageIcon aria-hidden="true" size={30} /> : null}
              {asset.mediaType === "video" && !source ? <Video aria-hidden="true" size={30} /> : null}
            </div>
            <div className="director-asset-card__body">
              <strong>{asset.triggerId ?? "角色参考"} · {mediaLabel(asset.mediaType)}</strong>
              <AssetStatusBadge status={status.status} label={status.label} />
              <span><code>{asset.assetId}</code></span>
              <span>hash <code>{conciseHash(asset.sha256)}</code> · {asset.verification.status}</span>
              {!asset.reviewMutable ? <span>发布包只读媒体</span> : null}
            </div>
          </li>
        );
      })}</ul> : <p className="empty-state">当前没有图片、音频或视频媒体。</p>}
    </section>
  );
};
