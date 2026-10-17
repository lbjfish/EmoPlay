import { ImageOff, MapPin, Sparkles } from "lucide-react";

import { assetVisualStatus, safeMediaUrl, type Asset, type Scene } from "../../api/contracts";
import { formatUtc } from "../../view-models/presentation";

interface StoryStageProps {
  scene: Scene | null;
  assets: Asset[];
  onOpenAsset: (assetId: string) => void;
}

export const StoryStage = ({ scene, assets, onOpenAsset }: StoryStageProps) => {
  const hero = assets
    .filter((asset) => asset.mediaType === "image" && assetVisualStatus(asset) === "final" && safeMediaUrl(asset))
    .sort((left, right) => {
      const score = (asset: Asset) =>
        (assetVisualStatus(asset) === "final" ? 100 : 0)
        + (asset.role === "background" ? 10 : asset.role === "candidate" ? 5 : 0);
      return score(right) - score(left);
    })[0];
  const source = hero ? safeMediaUrl(hero) : null;
  const storyTitle = scene?.trigger.intent || scene?.trigger.kind || "当前时刻";
  return (
    <section className="story-stage" aria-labelledby="story-stage-heading">
      <div className="story-stage__paperclip" aria-hidden="true" />
      <div className="story-stage__art" data-has-media={source ? "true" : "false"}>
        {hero && source ? (
          <button type="button" className="story-stage__image-button" onClick={() => onOpenAsset(hero.assetId)}>
            <img src={source} alt={`章节画面：${storyTitle}`} />
            <span>查看大图</span>
          </button>
        ) : (
          <div className="story-stage__absence">
            <ImageOff aria-hidden="true" size={34} />
            <strong>这一章的画面正在赶来</strong>
            <span>你仍可以先读对话，继续故事。</span>
          </div>
        )}
      </div>
      <div className="story-stage__copy">
        <p className="eyebrow"><Sparkles aria-hidden="true" size={14} />雨后片刻</p>
        <h1 id="story-stage-heading">{storyTitle}</h1>
        <p className="story-stage__time"><MapPin aria-hidden="true" size={16} />{formatUtc(scene?.trigger.atUtc ?? null)}</p>
        <p>{scene?.trigger.why || "当前章节尚未提供可展示的情绪说明。"}</p>
      </div>
    </section>
  );
};
