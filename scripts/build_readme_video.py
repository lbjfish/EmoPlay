#!/usr/bin/env python3
"""Build the README operation-guide MP4, GIF preview, and cover image."""

from __future__ import annotations

import argparse
import math
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

try:
    from PIL import (  # type: ignore[import-not-found]
        Image,
        ImageDraw,
        ImageEnhance,
        ImageFilter,
        ImageFont,
    )
except ImportError as exc:  # pragma: no cover - optional authoring dependency
    raise SystemExit("Pillow is required to rebuild the README guide video") from exc


WIDTH = 1280
HEIGHT = 720
FPS = 12
NAVY = (12, 31, 48)
BLUE = (38, 112, 158)
LEMON = (244, 193, 53)
PAPER = (255, 249, 239)
INK = (25, 37, 50)
MUTED = (186, 201, 214)


@dataclass(frozen=True)
class Scene:
    kind: str
    duration: float
    step: int
    title: str
    subtitle: str
    image: str | None = None
    focus_y: float = 0.5
    pulse: tuple[int, int] | None = None
    command: str | None = None


def _font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(path), size=size, index=0)


def _cover(image: Image.Image, size: tuple[int, int], *, focus_y: float) -> Image.Image:
    target_w, target_h = size
    scale = max(target_w / image.width, target_h / image.height)
    resized = image.resize(
        (round(image.width * scale), round(image.height * scale)),
        Image.Resampling.LANCZOS,
    )
    left = max(0, (resized.width - target_w) // 2)
    max_top = max(0, resized.height - target_h)
    top = round(max_top * min(1.0, max(0.0, focus_y)))
    return resized.crop((left, top, left + target_w, top + target_h))


def _fit(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    target_w, target_h = size
    scale = min(target_w / image.width, target_h / image.height)
    return image.resize(
        (round(image.width * scale), round(image.height * scale)),
        Image.Resampling.LANCZOS,
    )


def _wrap(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    width: int,
) -> list[str]:
    lines: list[str] = []
    current = ""
    for char in text:
        candidate = current + char
        if current and draw.textlength(candidate, font=font) > width:
            lines.append(current)
            current = char
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def _gradient() -> Image.Image:
    image = Image.new("RGB", (WIDTH, HEIGHT), NAVY)
    draw = ImageDraw.Draw(image)
    for y in range(HEIGHT):
        ratio = y / (HEIGHT - 1)
        color = (
            round(12 + 14 * ratio),
            round(31 + 24 * ratio),
            round(48 + 28 * ratio),
        )
        draw.line((0, y, WIDTH, y), fill=color)
    return image


def _progress(draw: ImageDraw.ImageDraw, step: int) -> None:
    count = 5
    width = 116
    gap = 12
    start = WIDTH - 48 - count * width - (count - 1) * gap
    for index in range(count):
        fill = LEMON if index < step else (83, 106, 124)
        draw.rounded_rectangle(
            (start + index * (width + gap), 24, start + index * (width + gap) + width, 31),
            radius=4,
            fill=fill,
        )


def _fade(image: Image.Image, local: float, duration: float) -> Image.Image:
    opacity = min(1.0, local / 0.45, (duration - local) / 0.45)
    if opacity >= 1:
        return image
    return Image.blend(Image.new("RGB", image.size, NAVY), image, max(0.0, opacity))


def _render_title(
    scene: Scene,
    local: float,
    fonts: dict[str, ImageFont.FreeTypeFont],
    story: Image.Image,
) -> Image.Image:
    background = _cover(story, (WIDTH, HEIGHT), focus_y=0.15).filter(
        ImageFilter.GaussianBlur(14)
    )
    background = ImageEnhance.Brightness(background).enhance(0.25)
    overlay = Image.new("RGBA", (WIDTH, HEIGHT), (8, 28, 44, 90))
    image = Image.alpha_composite(background.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((88, 108, 1192, 612), radius=36, fill=(12, 31, 48))
    draw.rounded_rectangle((112, 132, 174, 194), radius=31, fill=LEMON)
    draw.text((131, 136), "♪", font=fonts["icon"], fill=INK)
    draw.text((112, 238), scene.title, font=fonts["hero"], fill=PAPER)
    draw.text((116, 326), scene.subtitle, font=fonts["sub"], fill=MUTED)
    draw.rounded_rectangle((112, 418, 467, 476), radius=28, fill=(38, 112, 158))
    draw.text((143, 429), "离线运行 · 无需模型密钥", font=fonts["small"], fill=PAPER)
    draw.text((112, 536), "lbjfish / EmoPlay", font=fonts["tiny"], fill=(139, 162, 180))
    return _fade(image, local, scene.duration)


def _render_command(
    scene: Scene,
    local: float,
    fonts: dict[str, ImageFont.FreeTypeFont],
) -> Image.Image:
    image = _gradient()
    draw = ImageDraw.Draw(image)
    _progress(draw, scene.step)
    draw.text((64, 82), f"0{scene.step}", font=fonts["number"], fill=LEMON)
    draw.text((154, 91), scene.title, font=fonts["title"], fill=PAPER)
    draw.text((66, 163), scene.subtitle, font=fonts["body"], fill=MUTED)
    draw.rounded_rectangle((64, 242, 1216, 514), radius=28, fill=(5, 17, 28), outline=(46, 87, 113), width=2)
    draw.ellipse((91, 268, 107, 284), fill=(255, 99, 91))
    draw.ellipse((119, 268, 135, 284), fill=(255, 190, 62))
    draw.ellipse((147, 268, 163, 284), fill=(48, 209, 88))
    command = scene.command or ""
    for index, line in enumerate(command.splitlines()):
        color = LEMON if line.startswith("$") else (205, 225, 237)
        draw.text((96, 326 + index * 48), line, font=fonts["code"], fill=color)
    pulse = 0.5 + 0.5 * math.sin(local * math.pi * 2)
    draw.rounded_rectangle(
        (64, 566, 64 + round(1152 * min(1.0, local / max(0.1, scene.duration - 0.5))), 574),
        radius=4,
        fill=(244, round(166 + 27 * pulse), round(42 + 11 * pulse)),
    )
    return _fade(image, local, scene.duration)


def _render_screenshot(
    scene: Scene,
    local: float,
    fonts: dict[str, ImageFont.FreeTypeFont],
    source: Image.Image,
    story: Image.Image,
) -> Image.Image:
    if source.height > source.width * 1.5:
        background = _cover(story, (WIDTH, HEIGHT), focus_y=0.18).filter(
            ImageFilter.GaussianBlur(18)
        )
        image = ImageEnhance.Brightness(background).enhance(0.22)
        phone = _fit(source, (335, 626))
        phone_x = 96
        phone_y = (HEIGHT - phone.height) // 2
        canvas = image.convert("RGBA")
        shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        ImageDraw.Draw(shadow).rounded_rectangle(
            (phone_x - 14, phone_y - 14, phone_x + phone.width + 14, phone_y + phone.height + 14),
            radius=36,
            fill=(0, 0, 0, 110),
        )
        canvas = Image.alpha_composite(canvas, shadow.filter(ImageFilter.GaussianBlur(10)))
        canvas.paste(phone.convert("RGBA"), (phone_x, phone_y))
        image = canvas.convert("RGB")
        draw = ImageDraw.Draw(image)
        _progress(draw, scene.step)
        draw.text((510, 172), f"0{scene.step}", font=fonts["number"], fill=LEMON)
        draw.text((598, 181), scene.title, font=fonts["title"], fill=PAPER)
        y = 270
        for line in _wrap(draw, scene.subtitle, fonts["body"], 590):
            draw.text((514, y), line, font=fonts["body"], fill=MUTED)
            y += 46
        draw.rounded_rectangle((514, 448, 1128, 520), radius=24, fill=(38, 112, 158))
        draw.text((548, 465), "响应式布局 · 章节进度同步", font=fonts["small"], fill=PAPER)
        return _fade(image, local, scene.duration)

    progress = local / max(0.1, scene.duration)
    zoom = 1.0 + 0.025 * progress
    crop_w = max(1, round(source.width / zoom))
    crop_h = max(1, round(source.height / zoom))
    left = (source.width - crop_w) // 2
    max_top = source.height - crop_h
    top = round(max_top * scene.focus_y)
    moving = source.crop((left, top, left + crop_w, top + crop_h))
    image = _cover(moving, (WIDTH, HEIGHT), focus_y=scene.focus_y)
    image = ImageEnhance.Brightness(image).enhance(0.78)
    overlay = Image.new("RGBA", (WIDTH, HEIGHT), (7, 18, 28, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    overlay_draw.rectangle((0, 510, WIDTH, HEIGHT), fill=(7, 20, 31, 225))
    image = Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(image)
    _progress(draw, scene.step)
    draw.rounded_rectangle((48, 538, 130, 620), radius=21, fill=LEMON)
    draw.text((62, 548), f"0{scene.step}", font=fonts["number"], fill=INK)
    draw.text((158, 536), scene.title, font=fonts["title"], fill=PAPER)
    draw.text((160, 600), scene.subtitle, font=fonts["small"], fill=MUTED)
    if scene.pulse:
        x, y = scene.pulse
        radius = 17 + round(8 * (0.5 + 0.5 * math.sin(local * math.pi * 2)))
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), outline=LEMON, width=5)
        draw.ellipse((x - 5, y - 5, x + 5, y + 5), fill=LEMON)
    return _fade(image, local, scene.duration)


def _build_frames(
    *,
    root: Path,
    frames: Path,
    font_path: Path,
) -> tuple[int, Image.Image]:
    assets = root / "docs" / "assets"
    images = {
        name: Image.open(assets / name).convert("RGB")
        for name in (
            "emoplay-story.png",
            "emoplay-voice.png",
            "emoplay-mobile.png",
            "emoplay-director.png",
        )
    }
    fonts = {
        "hero": _font(font_path, 62),
        "title": _font(font_path, 38),
        "sub": _font(font_path, 29),
        "body": _font(font_path, 30),
        "small": _font(font_path, 24),
        "tiny": _font(font_path, 19),
        "number": _font(font_path, 42),
        "icon": _font(font_path, 36),
        "code": ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", size=25),
    }
    scenes = (
        Scene("title", 2.6, 0, "EmoPlay 操作视频", "从第一章到导演台，快速了解核心操作"),
        Scene(
            "command",
            3.3,
            1,
            "启动离线游戏",
            "安装后只需启动 Python 服务，终端会返回本地地址。",
            command="$ emoplay serve --config config.offline.toml \\\n  --materials materials/sample --port 0",
        ),
        Scene(
            "image",
            4.0,
            2,
            "从第一章开始",
            "新存档仅解锁 T1；确认后逐章推进，已解锁章节可随时回看。",
            image="emoplay-story.png",
            focus_y=0.05,
            pulse=(115, 164),
        ),
        Scene(
            "image",
            4.0,
            3,
            "查看图片与声音",
            "语音章节直接播放 WAV；照片支持查看大图和章节收藏。",
            image="emoplay-voice.png",
            focus_y=0.1,
            pulse=(357, 159),
        ),
        Scene(
            "image",
            3.4,
            4,
            "移动端同样可用",
            "章节栏自动定位当前进度，主图和操作保持在可视区域。",
            image="emoplay-mobile.png",
        ),
        Scene(
            "image",
            4.0,
            5,
            "进入导演台",
            "查看连续性数据、媒体来源和运行审计。",
            image="emoplay-director.png",
            focus_y=0.02,
            pulse=(955, 175),
        ),
        Scene(
            "command",
            3.4,
            5,
            "恢复全新存档",
            "停止服务后安全清空本地进度，最终图片和声音不会被删除。",
            command="$ emoplay init --config config.offline.toml \\\n  --materials materials/sample --reset",
        ),
    )

    frames.mkdir(parents=True, exist_ok=True)
    frame_index = 0
    cover: Image.Image | None = None
    for scene in scenes:
        count = round(scene.duration * FPS)
        for local_index in range(count):
            local = local_index / FPS
            if scene.kind == "title":
                frame = _render_title(scene, local, fonts, images["emoplay-story.png"])
            elif scene.kind == "command":
                frame = _render_command(scene, local, fonts)
            else:
                assert scene.image is not None
                frame = _render_screenshot(
                    scene,
                    local,
                    fonts,
                    images[scene.image],
                    images["emoplay-story.png"],
                )
            if cover is None and local > 0.8:
                cover = frame.copy()
            frame.save(
                frames / f"frame-{frame_index:04d}.jpg",
                format="JPEG",
                quality=91,
                optimize=True,
            )
            frame_index += 1
    assert cover is not None
    return frame_index, cover


def _run(command: list[str]) -> None:
    subprocess.run(command, check=True)


def _imageio_ffmpeg() -> Path | None:
    try:
        import imageio_ffmpeg  # type: ignore[import-not-found,import-untyped]
    except ImportError:
        return None
    return Path(imageio_ffmpeg.get_ffmpeg_exe())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", nargs="?", type=Path, default=Path("."))
    parser.add_argument("--ffmpeg", type=Path, default=os.environ.get("FFMPEG"))
    parser.add_argument(
        "--font",
        type=Path,
        default=Path("/System/Library/Fonts/PingFang.ttc"),
    )
    args = parser.parse_args()
    root = args.root.resolve()
    system_ffmpeg = shutil.which("ffmpeg")
    ffmpeg = (
        Path(args.ffmpeg)
        if args.ffmpeg
        else (Path(system_ffmpeg) if system_ffmpeg else _imageio_ffmpeg())
    )
    if ffmpeg is None or not Path(ffmpeg).is_file():
        raise SystemExit("ffmpeg is required; pass --ffmpeg or set FFMPEG")
    if not args.font.is_file():
        raise SystemExit(f"font not found: {args.font}")

    assets = root / "docs" / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    mp4 = assets / "emoplay-guide.mp4"
    gif = assets / "emoplay-guide.gif"
    cover_path = assets / "emoplay-guide-cover.png"
    with tempfile.TemporaryDirectory(prefix="emoplay-guide-") as temporary:
        frames = Path(temporary)
        frame_count, cover = _build_frames(root=root, frames=frames, font_path=args.font)
        cover.save(cover_path, format="PNG", optimize=True)
        _run(
            [
                str(ffmpeg),
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-framerate",
                str(FPS),
                "-i",
                str(frames / "frame-%04d.jpg"),
                "-vf",
                "scale=in_range=pc:out_range=tv,format=yuv420p",
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "22",
                "-pix_fmt",
                "yuv420p",
                "-color_range",
                "tv",
                "-movflags",
                "+faststart",
                str(mp4),
            ]
        )
        _run(
            [
                str(ffmpeg),
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(mp4),
                "-filter_complex",
                (
                    "fps=4,scale=720:-1:flags=lanczos,split[s0][s1];"
                    "[s0]palettegen=max_colors=96[p];"
                    "[s1][p]paletteuse=dither=bayer:bayer_scale=4"
                ),
                "-loop",
                "0",
                str(gif),
            ]
        )
    print(f"frames={frame_count}")
    for path in (cover_path, gif, mp4):
        print(f"{path.relative_to(root)} {path.stat().st_size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
