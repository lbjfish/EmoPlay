import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { GameShell } from "../app/GameShell";
import { dashboardWireFixture } from "./fixtures";

const response = (body: unknown) => new Response(JSON.stringify(body), {
  status: 200,
  headers: { "Content-Type": "application/json" },
});

describe("GameShell Story and God boundary", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("requests global assets only after explicit God entry and clears Story spoilers on exit", async () => {
    const calls: string[] = [];
    const globalAssets = dashboardWireFixture("god").assets.map((asset) => ({
      ...asset,
      asset_id: "reference-voice",
      trigger_id: null,
      media_type: "audio",
      preview_url: "/media/reference-voice",
    }));
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL) => {
      const path = String(input);
      calls.push(path);
      if (path.startsWith("/api/v1/assets")) {
        return response({ assets: globalAssets });
      }
      return response(dashboardWireFixture(path.includes("view=god") ? "god" : "story"));
    }));

    render(<GameShell />);
    await screen.findByText("当前可见消息");
    expect(screen.getByText("故事进度")).toBeTruthy();
    expect(screen.getByRole("button", { name: "继续第 2 章" })).toBeTruthy();
    expect(screen.queryByText(/书签/)).toBeNull();
    expect(screen.queryByText(/导入存档|导出存档/)).toBeNull();
    expect(screen.queryByText("未来绝不可在 Story 出现的正文")).toBeNull();
    expect(screen.queryByText("锁定的未来意图")).toBeNull();
    expect(calls.some((path) => path.startsWith("/api/v1/assets"))).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: "进入导演台" }));
    fireEvent.click(screen.getByRole("button", { name: "确认进入" }));
    await screen.findByText("导演台");
    expect(screen.queryByText(/保存书签|创建分支|切换分支|记录功能选择/)).toBeNull();
    expect(screen.queryByText(/Accept · 接受|Reject · 拒绝|核对 SHA 后接受|记录拒绝/)).toBeNull();
    expect(screen.queryByText(/能力边界|Provider capability/)).toBeNull();
    await waitFor(() => expect(screen.getAllByText(/reference-voice/).length).toBeGreaterThan(0));
    expect(screen.getByText("未来绝不可在 Story 出现的正文")).toBeTruthy();
    expect(calls.some((path) => path.startsWith("/api/v1/assets"))).toBe(true);

    fireEvent.click(screen.getByRole("button", { name: "退出导演台" }));
    await waitFor(() => expect(screen.getByText("当前可见消息")).toBeTruthy());
    expect(screen.queryByText("未来绝不可在 Story 出现的正文")).toBeNull();
    expect(screen.queryAllByText(/reference-voice/)).toHaveLength(0);
  });
});
