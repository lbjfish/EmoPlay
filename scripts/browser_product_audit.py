#!/usr/bin/env python3
"""Click through the local product in real Chrome and capture visual evidence."""

from __future__ import annotations

import argparse
import base64
import json
import time
from pathlib import Path
from typing import Any

from e2e.test_react_browser_shell import (
    CdpClient,
    ChromeProcess,
    _chrome_path,
    _fatal_console_errors,
    _unexpected_http_errors,
)

CHAPTER_EXPECTATIONS = {
    "T1": ("image", "行吧 就一眼"),
    "T2": ("audio", "早安语音"),
    "T3": ("image", "旺财是那只狗"),
    "T4": ("image", "拍点现场看看"),
    "T5": ("audio", "嗓子真的哑了"),
    "T6": ("image", "你认真的"),
}


def _click_text(client: CdpClient, text: str) -> bool:
    return bool(
        client.evaluate(
            f"""
(() => {{
  const button = [...document.querySelectorAll("button")]
    .find((item) => item.textContent?.includes({json.dumps(text, ensure_ascii=False)}));
  button?.click();
  return Boolean(button);
}})()
"""
        )
    )


def _capture_viewport(client: CdpClient, path: Path) -> None:
    result = client.command(
        "Page.captureScreenshot",
        {"format": "png", "fromSurface": True, "captureBeyondViewport": False},
        timeout=15,
    )
    data = result.get("data")
    if not isinstance(data, str):
        raise TypeError("Chrome did not return screenshot data")
    path.write_bytes(base64.b64decode(data))


def _screenshot(client: CdpClient, path: Path) -> None:
    client.evaluate("window.scrollTo(0, 0)")
    time.sleep(0.15)
    _capture_viewport(client, path)


def _chapter_snapshot(client: CdpClient, trigger_id: str) -> dict[str, Any]:
    media_type, expected_text = CHAPTER_EXPECTATIONS[trigger_id]
    return dict(
        client.evaluate(
            rf"""
(() => {{
  const current = document.querySelector(".chapter-marker.is-current");
  const chapterRail = document.querySelector(".chapter-rail");
  const stage = document.querySelector(".story-stage");
  const hero = stage?.querySelector(".story-stage__image-button img");
  const trigger = document.querySelector(".message-slip.is-trigger");
  const triggerImage = trigger?.querySelector(".message-media--image img");
  const triggerAudio = trigger?.querySelector("audio");
  const media = {json.dumps(media_type)} === "image" ? triggerImage : triggerAudio;
  const stageRect = stage?.getBoundingClientRect();
  const heroRect = hero?.getBoundingClientRect();
  const currentRect = current?.getBoundingClientRect();
  const railRect = chapterRail?.getBoundingClientRect();
  const locked = [...document.querySelectorAll(".chapter-marker")]
    .map((item) => item.classList.contains("is-locked"));
  return {{
    current: current?.textContent || "",
    expectedTextVisible: (document.body?.innerText || "").includes({json.dumps(expected_text, ensure_ascii=False)}),
    heroLoaded: Boolean(hero && hero.complete && hero.naturalWidth > 0),
    triggerMediaPresent: Boolean(media),
    triggerImageLoaded: triggerImage ? triggerImage.complete && triggerImage.naturalWidth > 0 : null,
    audioReadyState: triggerAudio?.readyState ?? null,
    audioNetworkState: triggerAudio?.networkState ?? null,
    audioError: triggerAudio?.error?.code ?? null,
    audioDuration: Number.isFinite(triggerAudio?.duration) ? triggerAudio.duration : null,
    aiffSupport: triggerAudio?.canPlayType("audio/aiff") || "",
    locked,
    noHorizontalOverflow: document.documentElement.scrollWidth <= window.innerWidth + 1,
    heroContained: Boolean(stageRect && heroRect && heroRect.left >= stageRect.left - 1 && heroRect.right <= stageRect.right + 1),
    currentChapterVisible: Boolean(currentRect && railRect && currentRect.left >= railRect.left - 1 && currentRect.right <= railRect.right + 1),
    productCopyClean: !/(场景仍需人工审核|证据项：|登记媒体|\[非文本消息)/u.test(document.body?.innerText || ""),
    progressControlsClean: Boolean(document.querySelector(".story-quick-actions"))
      && !/(保存本地书签|保存命名书签|导入存档|导出存档)/u.test(document.body?.innerText || ""),
  }};
}})()
"""
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    chrome = _chrome_path()
    if chrome is None:
        raise SystemExit("Chrome is unavailable")
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {"url": args.url, "chapters": {}, "director": {}}

    with ChromeProcess(chrome, output_dir / "chrome-profile") as browser, CdpClient(
        browser.new_page_websocket()
    ) as client:
        for method in ("Runtime.enable", "Log.enable", "Network.enable", "Page.enable"):
            client.command(method)
        client.command(
            "Emulation.setDeviceMetricsOverride",
            {"width": 1440, "height": 1000, "deviceScaleFactor": 1, "mobile": False},
        )
        client.command("Page.navigate", {"url": args.url})
        client.wait_for(
            'Boolean(document.body?.innerText.includes("柠柠的六幕故事") && document.querySelector(".chapter-marker.is-current"))'
        )

        for index, trigger_id in enumerate(CHAPTER_EXPECTATIONS, start=1):
            media_type, _ = CHAPTER_EXPECTATIONS[trigger_id]
            client.wait_for(
                f'document.querySelector(".chapter-marker.is-current")?.textContent.includes("第 {index} 章")'
            )
            client.wait_for(
                'document.querySelector(".story-stage__image-button img")?.complete === true'
            )
            time.sleep(0.2)
            snapshot = _chapter_snapshot(client, trigger_id)
            if media_type == "audio":
                snapshot["playback"] = client.evaluate(
                    """
(async () => {
  const audio = document.querySelector(".message-slip.is-trigger audio");
  if (!audio) return {ok: false, reason: "missing"};
  try {
    audio.currentTime = 0;
    await audio.play();
    await new Promise((resolve) => setTimeout(resolve, 450));
    const currentTime = audio.currentTime;
    const paused = audio.paused;
    audio.pause();
    return {ok: !paused && currentTime > 0, currentTime, error: audio.error?.code ?? null};
  } catch (error) {
    return {ok: false, reason: error instanceof Error ? error.name : String(error)};
  }
})()
"""
                )
            report["chapters"][trigger_id] = snapshot
            _screenshot(client, output_dir / f"story-{trigger_id}-desktop.png")
            if trigger_id in {"T1", "T2", "T5"}:
                client.evaluate(
                    'document.querySelector(".memory-deck")?.scrollIntoView({block:"center"})'
                )
                time.sleep(0.2)
                _capture_viewport(
                    client, output_dir / f"story-{trigger_id}-media-desktop.png"
                )
            if not all(
                (
                    snapshot["expectedTextVisible"],
                    snapshot["heroLoaded"],
                    snapshot["triggerMediaPresent"],
                    snapshot["noHorizontalOverflow"],
                    snapshot["heroContained"],
                    snapshot["productCopyClean"],
                    snapshot["progressControlsClean"],
                )
            ):
                raise AssertionError(f"{trigger_id} product snapshot failed: {snapshot}")
            if media_type == "audio" and not (
                isinstance(snapshot["audioReadyState"], int)
                and snapshot["audioReadyState"] >= 1
                and snapshot["audioError"] is None
                and isinstance(snapshot["audioDuration"], (int, float))
                and snapshot["audioDuration"] > 0
                and snapshot["playback"]["ok"] is True
            ):
                raise AssertionError(f"{trigger_id} audio playback failed: {snapshot}")
            expected_locked = [chapter_index > index for chapter_index in range(1, 7)]
            if snapshot["locked"] != expected_locked:
                raise AssertionError(
                    f"{trigger_id} lock state {snapshot['locked']} != {expected_locked}"
                )
            if index < 6:
                if trigger_id == "T1":
                    client.evaluate(
                        'document.querySelector(".story-quick-actions")?.scrollIntoView({block:"center"})'
                    )
                    time.sleep(0.2)
                    _capture_viewport(
                        client, output_dir / "story-T1-progress-desktop.png"
                    )
                if not _click_text(client, f"继续第 {index + 1} 章"):
                    raise AssertionError(f"next chapter button missing after {trigger_id}")
                client.wait_for('document.body?.innerText.includes("解锁下一章")')
                if not _click_text(client, "解锁下一章"):
                    raise AssertionError("chapter confirmation button missing")
                client.wait_for(
                    f'document.querySelector(".chapter-marker.is-current")?.textContent.includes("第 {index + 1} 章")',
                    timeout=15,
                )

        if not _click_text(client, "第 2 章"):
            raise AssertionError("unlocked chapter 2 could not be revisited")
        client.wait_for(
            'document.querySelector(".chapter-marker.is-current")?.textContent.includes("第 2 章")'
        )
        revisit_locks = client.evaluate(
            "[...document.querySelectorAll('.chapter-marker')].map((item) => item.classList.contains('is-locked'))"
        )
        report["revisit_after_completion"] = {"T2_locks": revisit_locks}
        if revisit_locks != [False, False, False, False, False, False]:
            raise AssertionError(f"revisiting T2 re-locked chapters: {revisit_locks}")
        if not _click_text(client, "第 6 章"):
            raise AssertionError("chapter 6 could not be restored after revisit")
        client.wait_for(
            'document.querySelector(".chapter-marker.is-current")?.textContent.includes("第 6 章")'
        )

        client.command(
            "Emulation.setDeviceMetricsOverride",
            {"width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": True},
        )
        time.sleep(0.25)
        mobile = _chapter_snapshot(client, "T6")
        report["mobile_T6"] = mobile
        _screenshot(client, output_dir / "story-T6-mobile.png")
        if not (
            mobile["noHorizontalOverflow"]
            and mobile["heroContained"]
            and mobile["currentChapterVisible"]
        ):
            raise AssertionError(f"mobile layout overflow: {mobile}")

        client.command(
            "Emulation.setDeviceMetricsOverride",
            {"width": 1440, "height": 1000, "deviceScaleFactor": 1, "mobile": False},
        )
        if not _click_text(client, "进入导演台"):
            raise AssertionError("director entry button missing")
        client.wait_for('document.body?.innerText.includes("进入导演台？")')
        if not _click_text(client, "确认进入"):
            raise AssertionError("director confirmation button missing")
        client.wait_for('document.body?.innerText.includes("导演媒体工作台")')
        director = dict(
            client.evaluate(
                """
(() => {
  const desk = document.querySelector(".director-desk");
  const deskRect = desk?.getBoundingClientRect();
  const jsonCards = [...document.querySelectorAll(".director-asset-card__preview--other")];
  const imageCards = [...document.querySelectorAll(".director-asset-card__preview--image")];
  const audioCards = [...document.querySelectorAll(".director-asset-card__preview--audio")];
  const disclosure = document.querySelector(".continuity-drawer .audit-disclosure");
  disclosure?.setAttribute("open", "");
  const pre = disclosure?.querySelector("pre");
  const preRect = pre?.getBoundingClientRect();
  const disclosureRect = disclosure?.getBoundingClientRect();
  return {
    jsonCards: jsonCards.length,
    imageCards: imageCards.length,
    audioCards: audioCards.length,
    pageContained: document.documentElement.scrollWidth <= window.innerWidth + 1,
    deskContained: Boolean(deskRect && deskRect.left >= -1 && deskRect.right <= window.innerWidth + 1),
    jsonContained: Boolean(preRect && disclosureRect && preRect.left >= disclosureRect.left - 1 && preRect.right <= disclosureRect.right + 1),
    obsoleteControlsAbsent: !/(保存书签|创建 checkpoint|检查回溯点|创建分支|切换分支|记录功能选择|Accept · 接受|Reject · 拒绝|核对 SHA 后接受|记录拒绝|能力边界|Provider capability)/u.test(document.body?.innerText || ""),
  };
})()
"""
            )
        )
        report["director"] = director
        client.evaluate('document.querySelector(".director-desk")?.scrollIntoView({block:"start"})')
        time.sleep(0.2)
        result = client.command(
            "Page.captureScreenshot",
            {"format": "png", "fromSurface": True, "captureBeyondViewport": False},
            timeout=15,
        )
        (output_dir / "director-desktop.png").write_bytes(
            base64.b64decode(str(result["data"]))
        )
        if not all(
            (
                director["pageContained"],
                director["deskContained"],
                director["jsonContained"],
                director["obsoleteControlsAbsent"],
            )
        ):
            raise AssertionError(f"director layout failed: {director}")

        origin = args.url.rstrip("/")
        external = [
            url
            for url in client.requests
            if url.startswith(("http://", "https://")) and not url.startswith(origin)
        ]
        report["external_requests"] = external
        report["http_errors"] = _unexpected_http_errors(client)
        report["console_errors"] = _fatal_console_errors(client)
        if external or report["http_errors"] or report["console_errors"]:
            raise AssertionError("browser errors detected")

    (output_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"status": "ok", "output_dir": str(output_dir)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
