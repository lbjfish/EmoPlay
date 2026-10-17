import { ImageOff, Paperclip, Volume2 } from "lucide-react";

import { assetVisualStatus, safeMediaUrl, type Asset, type Message, type MessageFlow } from "../../api/contracts";
import { formatUtc } from "../../view-models/presentation";
import { FutureLockBadge } from "../common/StatusBadge";

interface DialogueRailProps {
  flow: MessageFlow | null;
  god: boolean;
  assets: Asset[];
  script: string | null;
  remainingChapters: number;
  onOpenAsset: (assetId: string) => void;
}

const CHAPTER_CONTEXT_LIMIT = 9;

const hasTrigger = (message: { triggerId: string | null }): boolean => message.triggerId !== null;

const uniqueMessages = (messages: NonNullable<MessageFlow["visible"]>): NonNullable<MessageFlow["visible"]> => {
  const seen = new Set<string>();
  return messages.filter((message, index) => {
    const key = message.messageId || `${message.atUtc ?? "unknown"}-${index}`;
    if (seen.has(key)) {
      return false;
    }
    seen.add(key);
    return true;
  });
};

const storyChapterSlice = (flow: MessageFlow): NonNullable<MessageFlow["visible"]> => {
  const visible = flow.visible;
  if (!flow.trigger) {
    return uniqueMessages(visible.slice(-CHAPTER_CONTEXT_LIMIT));
  }
  const previousTriggerIndex = Math.max(...visible.map((message, index) => (hasTrigger(message) ? index : -1)));
  const chapterBeforeTrigger = visible.slice(previousTriggerIndex + 1).slice(-CHAPTER_CONTEXT_LIMIT);
  return uniqueMessages([...chapterBeforeTrigger, flow.trigger]);
};

const godChapterSlice = (flow: MessageFlow): NonNullable<MessageFlow["visible"]> => {
  if (!flow.all.length || !flow.trigger?.messageId) {
    return storyChapterSlice(flow);
  }
  const triggerIndex = flow.all.findIndex((message) => message.messageId === flow.trigger?.messageId);
  if (triggerIndex < 0) {
    return storyChapterSlice(flow);
  }
  const previousTriggerIndex = Math.max(
    ...flow.all.slice(0, triggerIndex).map((message, index) => (hasTrigger(message) ? index : -1)),
  );
  return uniqueMessages(flow.all.slice(previousTriggerIndex + 1, triggerIndex + 1).slice(-CHAPTER_CONTEXT_LIMIT));
};

const mediaTypeForMessage = (message: Message): Asset["mediaType"] | null => {
  if (message.kind === "image") {
    return "image";
  }
  if (message.kind === "voice" || message.kind === "audio") {
    return "audio";
  }
  if (message.kind === "video") {
    return "video";
  }
  return null;
};

const matchingAsset = (message: Message, assets: Asset[]): Asset | null => {
  const mediaType = mediaTypeForMessage(message);
  if (!mediaType || !message.triggerId) {
    return null;
  }
  const matching = assets.filter((asset) =>
    asset.triggerId === message.triggerId
    && asset.mediaType === mediaType
    && safeMediaUrl(asset)
    && assetVisualStatus(asset) !== "rejected",
  );
  return matching.find((asset) => assetVisualStatus(asset) === "final") ?? matching[0] ?? null;
};

const MessageBody = ({
  message,
  assets,
  script,
  onOpenAsset,
}: {
  message: Message;
  assets: Asset[];
  script: string | null;
  onOpenAsset: (assetId: string) => void;
}) => {
  if (message.text) {
    return <p>{message.text}</p>;
  }
  const asset = matchingAsset(message, assets);
  const source = asset ? safeMediaUrl(asset) : null;
  if (message.kind === "image") {
    return asset && source ? (
      <button type="button" className="message-media message-media--image" onClick={() => onOpenAsset(asset.assetId)}>
        <img src={source} alt="柠柠发来的照片" loading="lazy" />
        <span>点击查看照片</span>
      </button>
    ) : <div className="message-media message-media--missing"><ImageOff aria-hidden="true" size={22} /><span>这张照片暂时还没有载入。</span></div>;
  }
  if (message.kind === "voice" || message.kind === "audio") {
    return asset && source ? (
      <div className="message-media message-media--audio">
        <div><Volume2 aria-hidden="true" size={21} /><strong>柠柠的语音</strong></div>
        <audio controls preload="metadata" src={source}>当前浏览器无法播放这条语音。</audio>
      </div>
    ) : script ? (
      <div className="message-media message-media--transcript">
        <div><Volume2 aria-hidden="true" size={21} /><strong>语音文字</strong></div>
        <p>{script}</p>
      </div>
    ) : <div className="message-media message-media--missing"><Volume2 aria-hidden="true" size={22} /><span>这条语音暂时还没有载入。</span></div>;
  }
  if (message.kind === "video") {
    return asset && source ? <video className="message-media message-media--video" controls preload="metadata" src={source}>当前浏览器无法播放这段视频。</video> : <p>这段视频暂时还没有载入。</p>;
  }
  return <p>{message.kind ? `${message.kind} 消息` : "消息内容暂不可见"}</p>;
};

export const DialogueRail = ({ flow, god, assets, script, remainingChapters, onOpenAsset }: DialogueRailProps) => {
  const messages = flow ? (god ? godChapterSlice(flow) : storyChapterSlice(flow)) : [];
  const auditCount = god ? flow?.all.length ?? 0 : 0;
  return (
    <section className="notebook-section dialogue-rail" aria-labelledby="dialogue-heading">
      <header className="section-heading">
        <div>
          <p className="eyebrow">时间纸条</p>
          <h2 id="dialogue-heading">{god ? "本章审核对话" : "这一章的聊天"}</h2>
        </div>
        <FutureLockBadge count={god ? 0 : remainingChapters} />
      </header>
      {messages.length ? (
        <ol className="dialogue-rail__list">
          {messages.map((message, index) => {
            const trigger = message.messageId !== null && message.messageId === flow?.trigger?.messageId;
            return (
              <li key={message.messageId || `${message.atUtc ?? "unknown"}-${index}`} className={`message-slip${trigger ? " is-trigger" : ""}`}>
                {trigger ? <Paperclip className="message-slip__clip" aria-hidden="true" size={18} /> : null}
                <div className="message-slip__meta">
                  <strong>{message.name || message.role || "未标注角色"}</strong>
                  <span>{formatUtc(message.atUtc)} · {message.kind || "消息"}</span>
                </div>
                <MessageBody message={message} assets={assets} script={trigger ? script : null} onOpenAsset={onOpenAsset} />
                {trigger ? <span className="message-slip__trigger">这一刻</span> : null}
              </li>
            );
          })}
        </ol>
      ) : (
        <p className="empty-state">当前章节没有可展示的消息。</p>
      )}
      {god && flow?.all.length ? (
        <details className="audit-disclosure dialogue-rail__audit">
          <summary>展开完整审核对话流（{auditCount} 条）</summary>
          <ol className="dialogue-rail__list dialogue-rail__list--compact">
            {flow.all.map((message, index) => (
              <li key={message.messageId || `${message.atUtc ?? "unknown"}-${index}`} className="message-slip">
                <div className="message-slip__meta">
                  <strong>{message.name || message.role || "未标注角色"}</strong>
                  <span>{formatUtc(message.atUtc)} · {message.kind || "消息"}</span>
                </div>
                <MessageBody message={message} assets={assets} script={message.messageId === flow.trigger?.messageId ? script : null} onOpenAsset={onOpenAsset} />
              </li>
            ))}
          </ol>
        </details>
      ) : null}
      {!god && flow?.futureCount ? <p className="future-note">后面的对话会随着章节逐步展开。</p> : null}
    </section>
  );
};
