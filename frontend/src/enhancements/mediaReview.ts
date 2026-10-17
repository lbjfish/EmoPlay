import PhotoSwipeLightbox from "photoswipe/lightbox";
import WaveSurfer from "wavesurfer.js";

interface ReviewAsset {
  assetId: string;
  role: string;
  mediaType: "image" | "audio" | "video" | "other";
  previewAvailable: boolean;
  previewUrl: string | null;
  widthPx: number | null;
  heightPx: number | null;
}

const mediaPath = /^\/media\/[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/u;
const safeMediaUrl = (asset: ReviewAsset): string | null =>
  asset.previewAvailable && asset.previewUrl !== null && mediaPath.test(asset.previewUrl)
    ? asset.previewUrl
    : null;

const waveforms = new WeakMap<HTMLElement, WaveSurfer>();

const openImageReview = (asset: ReviewAsset, source: string): void => {
  const lightbox = new PhotoSwipeLightbox({
    dataSource: [{
      src: source,
      width: asset.widthPx || 1,
      height: asset.heightPx || 1,
      alt: `已授权图片媒体：${asset.role}`,
    }],
    pswpModule: () => import("photoswipe"),
    showAnimationDuration: 0,
    hideAnimationDuration: 0,
    zoomAnimationDuration: 0,
    escKey: true,
  });
  lightbox.init();
  lightbox.loadAndOpen(0);
};

const openAudioReview = (host: HTMLElement, source: string): void => {
  waveforms.get(host)?.destroy();
  host.replaceChildren();
  const waveform = WaveSurfer.create({
    container: host,
    url: source,
    autoplay: false,
    height: 64,
    waveColor: "#2E6F95",
    progressColor: "#A86616",
    cursorColor: "#1B2430",
    interact: true,
  });
  waveforms.set(host, waveform);
};

/** Enhancements accept only facade-authorized /media URLs; native controls remain the baseline. */
export const enhanceMediaReview = (asset: ReviewAsset, host: HTMLElement): string => {
  const source = safeMediaUrl(asset);
  if (!source) {
    return "没有可读取的已授权媒体，保留原生元数据复核。";
  }
  if (asset.mediaType === "image") {
    openImageReview(asset, source);
    return "已打开本地图片增强复核。按 Escape 返回。";
  }
  if (asset.mediaType === "audio") {
    openAudioReview(host, source);
    return "已加载本地音频波形；原生播放器仍可用。";
  }
  return "该媒体类型继续使用原生播放器。";
};
