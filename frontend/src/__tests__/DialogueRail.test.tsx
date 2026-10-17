import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DialogueRail } from "../components/story/DialogueRail";
import type { MessageFlow } from "../api/contracts";

const message = (
  messageId: string,
  text: string | null,
  triggerId: string | null = null,
) => ({
  messageId,
  atUtc: "2026-10-16T00:00:00Z",
  role: "persona",
  name: "柠柠",
  kind: triggerId ? "image" : "text",
  text,
  triggerId,
});

const flow: MessageFlow = {
  visible: [
    message("m001", "第一章旧消息 A"),
    message("m002", "第一章旧消息 B"),
    message("m003", null, "T1"),
    message("m004", "第二章应展示 A"),
    message("m005", "第二章应展示 B"),
  ],
  trigger: { ...message("m006", null, "T2"), kind: "voice" },
  futureCount: 2,
  futureLocked: [],
  all: [
    message("m001", "第一章旧消息 A"),
    message("m002", "第一章旧消息 B"),
    message("m003", null, "T1"),
    message("m004", "第二章应展示 A"),
    message("m005", "第二章应展示 B"),
    { ...message("m006", null, "T2"), kind: "voice" },
    message("m007", "未来正文"),
  ],
};

describe("DialogueRail", () => {
  it("shows only the current chapter slice in Story mode", () => {
    render(<DialogueRail flow={flow} god={false} assets={[]} script="早安，醒了记得吃早饭。" remainingChapters={2} onOpenAsset={() => undefined} />);

    const currentList = screen.getByRole("list");
    expect(within(currentList).queryByText("第一章旧消息 A")).toBeNull();
    expect(within(currentList).getByText("第二章应展示 A")).toBeTruthy();
    expect(within(currentList).getByText("第二章应展示 B")).toBeTruthy();
    expect(within(currentList).getByText("语音文字")).toBeTruthy();
    expect(within(currentList).getByText("早安，醒了记得吃早饭。")).toBeTruthy();
  });

  it("keeps God mode chapter-focused while offering the full audit flow behind details", () => {
    render(<DialogueRail flow={flow} god assets={[]} script="早安，醒了记得吃早饭。" remainingChapters={0} onOpenAsset={() => undefined} />);

    const currentList = screen.getAllByRole("list")[0];
    expect(within(currentList).queryByText("第一章旧消息 A")).toBeNull();
    expect(within(currentList).getByText("第二章应展示 A")).toBeTruthy();
    expect(screen.getByText("展开完整审核对话流（7 条）")).toBeTruthy();
  });
});
