import { describe, expect, it } from "vitest";

import { assetVisualStatus, parseDashboard, safeMediaUrl } from "../api/contracts";
import { dashboardWireFixture } from "./fixtures";

describe("presentation DTO guards", () => {
  it("drops an unexpected full message flow from a Story response", () => {
    const raw = dashboardWireFixture("story");
    raw.scene.message_flow.all = [{
      message_id: "M-late",
      at_utc: "2026-01-03T00:00:00Z",
      role: "b",
      name: "乙",
      kind: "text",
      text: "must not remain in Story memory",
      trigger_id: "T2",
    }];
    const dashboard = parseDashboard(raw);
    expect(dashboard.scene?.messageFlow.all).toEqual([]);
    expect(dashboard.timeline[1]?.intent).toBeNull();
  });

  it("accepts only facade media paths and does not infer final status", () => {
    const dashboard = parseDashboard(dashboardWireFixture("story"));
    const asset = dashboard.assets[0];
    expect(assetVisualStatus(asset)).toBe("candidate");
    expect(safeMediaUrl({ ...asset, previewUrl: "https://example.invalid/media" })).toBeNull();
    expect(assetVisualStatus({ ...asset, finalEligible: true })).toBe("candidate");
    expect(assetVisualStatus({ ...asset, reviewAction: "accept", finalEligible: true })).toBe("final");
  });
});
