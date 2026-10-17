from __future__ import annotations

from pathlib import Path

from scripts.summarize_metrics import summarize

ROOT = Path(__file__).resolve().parents[1]


def test_product_tuning_metrics_are_recomputed_from_committed_logs() -> None:
    metrics = summarize(ROOT)

    coverage = metrics["product_coverage_comparison"]
    assert coverage["before"]["chapter_backgrounds"]["count"] == 2
    assert coverage["after"]["chapter_backgrounds"]["count"] == 6
    assert coverage["before"]["primary_trigger_media"]["percent"] == 50.0
    assert coverage["after"]["primary_trigger_media"]["percent"] == 100.0
    assert coverage["percentage_point_change"]["chapter_backgrounds"] == 66.7

    browser = metrics["browser_audio_comparison"]
    assert browser["before"]["playable_count"] == 0
    assert browser["after"]["playable_count"] == 2

    image = metrics["gpt_image_2"]
    assert image["successful_call_count"] == 7
    assert image["total_estimated_cost_amount_micros"] == 1_724_952

    voice = metrics["tuning_comparison"]
    assert voice["before"]["duration_ms"] == 7_593
    assert voice["after"]["duration_ms"] == 5_132
    assert voice["reduction_percent"] == 32.4
