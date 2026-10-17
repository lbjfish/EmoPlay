from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_readme_operation_guide_assets_are_local_and_bounded() -> None:
    assets = ROOT / "docs" / "assets"
    cover = assets / "emoplay-guide-cover.png"
    preview = assets / "emoplay-guide.gif"
    video = assets / "emoplay-guide.mp4"

    assert cover.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert preview.read_bytes()[:6] in {b"GIF87a", b"GIF89a"}
    assert b"ftyp" in video.read_bytes()[:32]
    assert cover.stat().st_size < 1_000_000
    assert preview.stat().st_size < 5_000_000
    assert video.stat().st_size < 10_000_000

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "![EmoPlay 操作视频动画预览](docs/assets/emoplay-guide.gif)" in readme
    assert "](docs/assets/emoplay-guide.mp4)" in readme
