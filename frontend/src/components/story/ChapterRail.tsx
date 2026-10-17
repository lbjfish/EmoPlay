import { BookOpenText, LockKeyhole } from "lucide-react";
import { useEffect, useRef } from "react";

import type { TimelineEntry, ViewMode } from "../../api/contracts";
import { formatUtc } from "../../view-models/presentation";

interface ChapterRailProps {
  timeline: TimelineEntry[];
  selectedTriggerId: string | null;
  viewMode: ViewMode;
  disabled: boolean;
  onSelect: (triggerId: string) => void;
}

export const ChapterRail = ({
  timeline,
  selectedTriggerId,
  viewMode,
  disabled,
  onSelect,
}: ChapterRailProps) => {
  const currentButton = useRef<HTMLButtonElement | null>(null);
  useEffect(() => {
    const revealCurrent = () => {
      const button = currentButton.current;
      if (button && typeof button.scrollIntoView === "function") {
        button.scrollIntoView({ block: "nearest", inline: "center" });
      }
    };
    revealCurrent();
    window.addEventListener("resize", revealCurrent);
    return () => window.removeEventListener("resize", revealCurrent);
  }, [selectedTriggerId]);
  return (
    <nav className="chapter-rail" aria-label="章节轨">
      <p className="eyebrow">六幕故事 · 一章章解锁</p>
      <ol className="chapter-rail__list">
        {timeline.map((chapter, index) => {
          const locked = viewMode === "story" && chapter.locked;
          const selected = chapter.triggerId === selectedTriggerId;
          const label = locked
            ? `第 ${index + 1} 章，未来章节锁定`
            : `第 ${index + 1} 章，${chapter.intent || chapter.kind || "当前时刻"}`;
          return (
            <li key={chapter.triggerId}>
              <button
                ref={selected ? currentButton : null}
                type="button"
                className={`chapter-marker${selected ? " is-current" : ""}${locked ? " is-locked" : ""}`}
                aria-current={selected ? "step" : undefined}
                aria-label={label}
                disabled={disabled || locked}
                onClick={() => onSelect(chapter.triggerId)}
              >
                {locked ? <LockKeyhole aria-hidden="true" size={18} /> : <BookOpenText aria-hidden="true" size={18} />}
                <span className="chapter-marker__number">第 {index + 1} 章</span>
                <span className="chapter-marker__title">{locked ? "未来内容已锁定" : chapter.intent || chapter.kind || "当前时刻"}</span>
                <span className="chapter-marker__time">{formatUtc(chapter.atUtc)}</span>
              </button>
            </li>
          );
        })}
      </ol>
    </nav>
  );
};
