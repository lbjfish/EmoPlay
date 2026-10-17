import { ChevronLeft, ChevronRight, Headphones, Image as ImageIcon, Video } from "lucide-react";
import useEmblaCarousel from "embla-carousel-react";
import { useCallback, useEffect, useState } from "react";

import { assetVisualStatus, safeMediaUrl, type Asset } from "../../api/contracts";
import { mediaLabel } from "../../view-models/presentation";

interface MemoryDeckProps {
  assets: Asset[];
  onOpenAsset: (assetId: string) => void;
}

export const MemoryDeck = ({ assets, onOpenAsset }: MemoryDeckProps) => {
  const memories = assets.filter((asset) =>
    asset.mediaType !== "other"
    && assetVisualStatus(asset) === "final"
    && safeMediaUrl(asset) !== null,
  );
  const [emblaRef, emblaApi] = useEmblaCarousel({ align: "start", containScroll: "trimSnaps" });
  const [canPrevious, setCanPrevious] = useState(false);
  const [canNext, setCanNext] = useState(false);
  const syncControls = useCallback(() => {
    setCanPrevious(emblaApi?.canScrollPrev() ?? false);
    setCanNext(emblaApi?.canScrollNext() ?? false);
  }, [emblaApi]);

  useEffect(() => {
    if (!emblaApi) {
      return undefined;
    }
    const frame = window.requestAnimationFrame(syncControls);
    emblaApi.on("select", syncControls);
    emblaApi.on("reInit", syncControls);
    return () => {
      window.cancelAnimationFrame(frame);
      emblaApi.off("select", syncControls);
      emblaApi.off("reInit", syncControls);
    };
  }, [emblaApi, syncControls]);

  return (
    <section className="notebook-section memory-deck" aria-labelledby="memory-heading">
      <header className="section-heading">
        <div>
          <p className="eyebrow">本章收藏</p>
          <h2 id="memory-heading">照片与声音</h2>
        </div>
        <div className="carousel-controls" aria-label="卡组翻阅">
          <button type="button" className="icon-button" aria-label="上一张记忆卡" disabled={!canPrevious} onClick={() => emblaApi?.scrollPrev()}><ChevronLeft aria-hidden="true" size={19} /></button>
          <button type="button" className="icon-button" aria-label="下一张记忆卡" disabled={!canNext} onClick={() => emblaApi?.scrollNext()}><ChevronRight aria-hidden="true" size={19} /></button>
        </div>
      </header>
      {memories.length ? (
        <div className="memory-deck__viewport" ref={emblaRef}>
          <div className="memory-deck__container">
            {memories.map((asset) => {
              const source = safeMediaUrl(asset);
              return (
                <article className="memory-card" key={asset.assetId}>
                  <div className="memory-card__visual">
                    {asset.mediaType === "image" && source ? <img src={source} alt="本章照片" loading="lazy" /> : null}
                    {asset.mediaType === "audio" && source ? (
                      <div className="memory-card__audio">
                        <Headphones aria-hidden="true" size={32} />
                        <span>柠柠的语音</span>
                        <audio controls preload="metadata" src={source}>当前浏览器无法播放这条语音。</audio>
                      </div>
                    ) : null}
                    {asset.mediaType === "video" && source ? <video controls preload="metadata" src={source}>当前浏览器无法播放这段视频。</video> : null}
                  </div>
                  <div className="memory-card__body">
                    <p className="memory-card__kind">
                      {asset.mediaType === "image" ? <ImageIcon aria-hidden="true" size={16} /> : null}
                      {asset.mediaType === "audio" ? <Headphones aria-hidden="true" size={16} /> : null}
                      {asset.mediaType === "video" ? <Video aria-hidden="true" size={16} /> : null}
                      {mediaLabel(asset.mediaType)}
                    </p>
                    <p>{asset.mediaType === "image" ? "留在这一刻的画面。" : asset.mediaType === "audio" ? "点播放，听见这一刻。" : "播放这一段回忆。"}</p>
                    <button type="button" className="button button--quiet" onClick={() => onOpenAsset(asset.assetId)}>
                      {asset.mediaType === "image" ? "查看大图" : "打开播放器"}
                    </button>
                  </div>
                </article>
              );
            })}
          </div>
        </div>
      ) : (
        <p className="empty-state">这一章还没有留下照片或声音。</p>
      )}
    </section>
  );
};
