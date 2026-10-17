import { describe, expect, it } from "vitest";

import { buildMutationPayload, gameReducer, initialGameState } from "../state/gameReducer";
import { dashboardFixture } from "./fixtures";

describe("game mutation reducer", () => {
  it("anchors every selected mutation to the current checkpoint and SHA for accept", () => {
    const dashboard = dashboardFixture("god");
    const common = ["generate", "position", "review_accept", "review_reject"] as const;
    const forms = {
      generate: {},
      position: {},
      review_accept: { asset_id: "scene-image", reason: "人工核对通过" },
      review_reject: { asset_id: "scene-image", reason: "人工核对拒绝" },
    };
    for (const name of common) {
      const action = buildMutationPayload(name, forms[name], dashboard, "T1", `op-${name}`);
      expect(action.payload.expected_checkpoint_id).toBe("checkpoint-1");
      expect(action.payload.operation_id).toBe(`op-${name}`);
    }
    const acceptance = buildMutationPayload("review_accept", forms.review_accept, dashboard, "T1", "op-accept");
    expect(acceptance.payload.expected_sha256).toBe("a".repeat(64));
  });

  it("can advance Story by submitting only the next trigger id", () => {
    const dashboard = dashboardFixture("story");
    const action = buildMutationPayload(
      "position",
      { trigger_id: "T2", advance: "true" },
      dashboard,
      "T1",
      "op-next",
    );

    expect(action.label).toBe("继续下一章");
    expect(action.payload).toMatchObject({
      expected_checkpoint_id: "checkpoint-1",
      operation_id: "op-next",
      trigger_id: "T2",
      view_mode: "story",
    });
  });

  it("drops God-only snapshots before a Story request", () => {
    const god = dashboardFixture("god");
    const ready = gameReducer(initialGameState, { type: "resolved", requestId: 0, dashboard: god, godAssets: god.assets });
    const storyRequest = gameReducer(ready, { type: "request", requestId: 1, viewMode: "story" });
    expect(storyRequest.dashboard).toBeNull();
    expect(storyRequest.godAssets).toEqual([]);
  });
});
