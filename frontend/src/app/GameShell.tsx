import * as AlertDialog from "@radix-ui/react-alert-dialog";
import { CloudRain, Eye, Leaf, ShieldAlert, Sparkles, StepForward, X } from "lucide-react";
import { lazy, Suspense, useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";

import { loadAllAssets, loadDashboard, operationId, submitAction } from "../api/client";
import type { Asset, Dashboard, ViewMode } from "../api/contracts";
import {
  buildMutationPayload,
  gameReducer,
  initialGameState,
  type MutationName,
} from "../state/gameReducer";
import { ActionConfirmDialog } from "../components/common/ActionConfirmDialog";
import { ErrorState, LoadingState } from "../components/common/SectionState";
import { MediaDialog } from "../components/common/MediaDialog";
import { DirectorDesk } from "../components/god/DirectorDesk";
import { ChapterRail } from "../components/story/ChapterRail";
import { ContinuityDrawer } from "../components/story/ContinuityDrawer";
import { DialogueRail } from "../components/story/DialogueRail";
import { MemoryDeck } from "../components/story/MemoryDeck";
import { StoryStage } from "../components/story/StoryStage";

const EffectsLayer = lazy(() => import("../enhancements/effects"));
const GodCharts = lazy(() => import("../enhancements/godCharts"));

const humanError = (error: unknown): string =>
  error instanceof Error ? error.message : "本地资料暂时无法读取。";

const useReducedMotion = (): boolean => {
  const [reduced, setReduced] = useState(() =>
    typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches,
  );
  useEffect(() => {
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setReduced(query.matches);
    update();
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);
  return reduced;
};

const StoryQuickActions = ({
  dashboard,
  pending,
  onQueue,
  onNavigate,
}: {
  dashboard: Dashboard;
  pending: MutationName | null;
  onQueue: (name: MutationName, values: Record<string, string>) => void;
  onNavigate: (triggerId: string) => void;
}) => {
  const writable = dashboard.controls.state === "available";
  const selectedIndex = dashboard.timeline.findIndex((chapter) => chapter.triggerId === dashboard.selectedTriggerId);
  const nextChapter = selectedIndex >= 0 ? dashboard.timeline[selectedIndex + 1] ?? null : null;
  const nextLabel = nextChapter === null ? null : `第 ${selectedIndex + 2} 章`;
  return (
    <section className="story-quick-actions" aria-label="章节推进">
      <div>
        <p className="eyebrow">故事进度</p>
        <h2>{nextLabel ? `准备继续${nextLabel}` : "故事已经读完"}</h2>
        <p>{nextLabel ? "解锁下一章时会自动保存本地进度，无需额外操作。" : "你仍可从上方章节栏回看已经解锁的内容。"}</p>
      </div>
      <div className="story-quick-actions__advance">
        <button
          type="button"
          className="button button--dark"
          disabled={!writable || !dashboard.controls.positionAvailable || pending !== null || nextChapter === null}
          onClick={() => {
            if (!nextChapter) {
              return;
            }
            if (nextChapter.locked) {
              onQueue("position", { trigger_id: nextChapter.triggerId, advance: "true" });
            } else {
              onNavigate(nextChapter.triggerId);
            }
          }}
        >
          <StepForward aria-hidden="true" size={17} />{nextLabel ? `继续${nextLabel}` : "已到最后一章"}
        </button>
      </div>
    </section>
  );
};

export const GameShell = () => {
  const [state, dispatch] = useReducer(gameReducer, initialGameState);
  const [godEntryOpen, setGodEntryOpen] = useState(false);
  const requestSequence = useRef(0);
  const activeRequest = useRef<AbortController | null>(null);
  const reducedMotion = useReducedMotion();

  const requestDashboard = useCallback(async (viewMode: ViewMode, triggerId: string | null) => {
    activeRequest.current?.abort();
    const controller = new AbortController();
    activeRequest.current = controller;
    const requestId = requestSequence.current + 1;
    requestSequence.current = requestId;
    dispatch({ type: "request", requestId, viewMode });
    try {
      const dashboard = await loadDashboard(viewMode, triggerId, controller.signal);
      // This is intentionally requested only after an explicit God confirmation.
      // Story never requests or stores the all-assets response.
      const godAssets = viewMode === "god" ? await loadAllAssets(controller.signal) : [];
      if (!controller.signal.aborted) {
        dispatch({ type: "resolved", requestId, dashboard, godAssets });
      }
    } catch (error) {
      if (!controller.signal.aborted) {
        dispatch({ type: "failed", requestId, message: humanError(error) });
      }
    }
  }, []);

  useEffect(() => {
    void requestDashboard("story", null);
    return () => activeRequest.current?.abort();
  }, [requestDashboard]);

  const dashboard = state.dashboard;
  const visibleAssets = useMemo<Asset[]>(() => {
    if (!dashboard) {
      return [];
    }
    return state.viewMode === "god" ? state.godAssets : dashboard.assets;
  }, [dashboard, state.godAssets, state.viewMode]);
  const selectedAsset = visibleAssets.find((asset) => asset.assetId === state.selectedAssetId) ?? null;
  const remainingChapters = dashboard?.timeline.filter((chapter) => chapter.locked).length ?? 0;

  const queueAction = useCallback((name: MutationName, values: Record<string, string>) => {
    if (!dashboard) {
      return;
    }
    try {
      dispatch({
        type: "queueAction",
        action: buildMutationPayload(
          name,
          values,
          dashboard,
          dashboard.selectedTriggerId,
          operationId(),
          visibleAssets,
        ),
      });
    } catch (error) {
      dispatch({ type: "feedbackError", message: humanError(error) });
    }
  }, [dashboard, visibleAssets]);

  const confirmAction = useCallback(async () => {
    const queued = state.queuedAction;
    if (!queued) {
      return;
    }
    dispatch({ type: "actionPending", action: queued.name });
    try {
      await submitAction(queued.name, queued.payload);
      dispatch({
        type: "actionFinished",
        notice: queued.name === "position" && queued.label === "继续下一章"
          ? "下一章已解锁，正在展开最新内容。"
          : `${queued.label}已完成，正在展开最新内容。`,
      });
      await requestDashboard(state.viewMode, null);
    } catch (error) {
      dispatch({ type: "actionFailed", message: humanError(error) });
    }
  }, [requestDashboard, state.queuedAction, state.viewMode]);

  const openEnhancedReview = useCallback((asset: Asset, host: HTMLDivElement) => {
    void import("../enhancements/mediaReview")
      .then(({ enhanceMediaReview }) => {
        const message = enhanceMediaReview(asset, host);
        host.setAttribute("role", "status");
        host.setAttribute("aria-label", message);
      })
      .catch(() => dispatch({ type: "feedbackError", message: "增强复核无法加载；原生媒体控件仍可使用。" }));
  }, []);

  const charts = state.chartsRequested && dashboard ? (
    <Suspense fallback={<LoadingState label="正在按需加载本地指标组件…" />}>
      <GodCharts dashboard={dashboard} />
    </Suspense>
  ) : null;

  const enterGod = () => {
    setGodEntryOpen(false);
    void requestDashboard("god", dashboard?.selectedTriggerId ?? null);
  };

  const exitGod = () => {
    // No selected God trigger is carried back: the backend selects the current
    // playable position, and the reducer drops the God-only response immediately.
    void requestDashboard("story", null);
  };

  return (
    <div className="game-shell">
      <a className="skip-link" href="#nining-main">跳到当前章节</a>
      {state.atmosphereEnabled && !reducedMotion ? (
        <Suspense fallback={null}><EffectsLayer /></Suspense>
      ) : null}
      <header className="game-header">
        <div className="game-header__brand">
          <span className="brand-mark" aria-hidden="true"><Leaf size={20} /></span>
          <div><p className="eyebrow">雨后柠檬手账</p><strong>柠柠的六幕故事</strong></div>
        </div>
        <div className="game-header__actions">
          <span className={`connection-state connection-state--${state.phase}`} role="status" aria-live="polite">
            {state.phase === "loading" ? "故事正在展开" : state.phase === "error" ? "故事暂时无法读取" : "故事已就绪"}
          </span>
          <button
            type="button"
            className="button button--quiet"
            aria-pressed={state.atmosphereEnabled}
            disabled={reducedMotion}
            onClick={() => dispatch({ type: "setAtmosphere", enabled: !state.atmosphereEnabled })}
          ><CloudRain aria-hidden="true" size={17} />{reducedMotion ? "系统已减少动效" : state.atmosphereEnabled ? "关闭雨滴氛围" : "开启雨滴氛围"}</button>
          {state.viewMode === "god" ? (
            <button type="button" className="button button--quiet" onClick={exitGod}><X aria-hidden="true" size={17} />退出导演台</button>
          ) : (
            <button type="button" className="button button--dark" onClick={() => setGodEntryOpen(true)}><Eye aria-hidden="true" size={17} />进入导演台</button>
          )}
        </div>
      </header>

      <AlertDialog.Root open={godEntryOpen} onOpenChange={setGodEntryOpen}>
        <AlertDialog.Portal>
          <AlertDialog.Overlay className="dialog-overlay" />
          <AlertDialog.Content className="dialog-card">
            <p className="eyebrow">含剧透确认</p>
            <AlertDialog.Title>进入导演台？</AlertDialog.Title>
            <AlertDialog.Description className="dialog-copy">导演台会额外读取完整时间线和所有已登记媒体，用于本机审核。退出时会清空该专用快照并重新请求 Story。</AlertDialog.Description>
            <div className="dialog-actions">
              <AlertDialog.Cancel asChild><button type="button" className="button button--quiet">留在 Story</button></AlertDialog.Cancel>
              <AlertDialog.Action asChild><button type="button" className="button button--dark" onClick={enterGod}><ShieldAlert aria-hidden="true" size={17} />确认进入</button></AlertDialog.Action>
            </div>
          </AlertDialog.Content>
        </AlertDialog.Portal>
      </AlertDialog.Root>

      <main id="nining-main" className={`game-layout game-layout--${state.viewMode}`} tabIndex={-1}>
        {state.phase === "loading" && !dashboard ? <LoadingState /> : null}
        {state.phase === "error" && !dashboard ? <ErrorState message={state.error || "本地资料无法读取。"} onRetry={() => void requestDashboard(state.viewMode, null)} /> : null}
        {dashboard ? (
          <>
            {state.error ? <p className="inline-error" role="alert">{state.error}</p> : null}
            {state.notice ? <p className="inline-notice" role="status">{state.notice}<button type="button" className="text-button" onClick={() => dispatch({ type: "clearNotice" })}>关闭</button></p> : null}
            {dashboard.empty ? <section className="empty-stage"><Sparkles aria-hidden="true" size={28} /><h1>尚无章节</h1><p>本地后端没有提供可展示的时刻；没有内容会被浏览器补写。</p></section> : (
              <>
                <ChapterRail
                  timeline={dashboard.timeline}
                  selectedTriggerId={dashboard.selectedTriggerId}
                  viewMode={state.viewMode}
                  disabled={state.phase === "loading" || state.pendingAction !== null}
                  onSelect={(triggerId) => void requestDashboard(state.viewMode, triggerId)}
                />
                <div className="story-column">
                  <StoryStage scene={dashboard.scene} assets={dashboard.assets} onOpenAsset={(assetId) => dispatch({ type: "selectAsset", assetId })} />
                  <DialogueRail
                    flow={dashboard.scene?.messageFlow ?? null}
                    god={state.viewMode === "god"}
                    assets={state.viewMode === "god" ? state.godAssets : dashboard.assets}
                    script={dashboard.scene?.trigger.say ?? null}
                    remainingChapters={remainingChapters}
                    onOpenAsset={(assetId) => dispatch({ type: "selectAsset", assetId })}
                  />
                  <MemoryDeck assets={dashboard.assets} onOpenAsset={(assetId) => dispatch({ type: "selectAsset", assetId })} />
                  {state.viewMode === "story" ? <ContinuityDrawer scene={dashboard.scene} god={false} remainingChapters={remainingChapters} /> : null}
                  {state.viewMode === "story" ? (
                    <StoryQuickActions
                      dashboard={dashboard}
                      pending={state.pendingAction}
                      onQueue={queueAction}
                      onNavigate={(triggerId) => void requestDashboard("story", triggerId)}
                    />
                  ) : null}
                </div>
                {state.viewMode === "god" ? (
                  <DirectorDesk
                    dashboard={dashboard}
                    assets={visibleAssets}
                    chartsRequested={state.chartsRequested}
                    charts={charts}
                    onRequestCharts={() => dispatch({ type: "requestCharts" })}
                    onOpenAsset={(assetId) => dispatch({ type: "selectAsset", assetId })}
                  />
                ) : null}
              </>
            )}
          </>
        ) : null}
      </main>
      <MediaDialog asset={selectedAsset} onOpenChange={(open) => !open && dispatch({ type: "selectAsset", assetId: null })} onEnhance={openEnhancedReview} />
      <ActionConfirmDialog action={state.queuedAction} pending={state.pendingAction !== null} onClose={() => dispatch({ type: "clearQueuedAction" })} onConfirm={() => void confirmAction()} />
    </div>
  );
};
