"""Assemble documentation GIFs from ordered PNG frame folders."""
from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
import re


DEFAULT_FRAME_PATTERN = "frame-*.png"
DEFAULT_DURATION_MS = 450


class GifAssemblyError(RuntimeError):
    """Raised when documentation GIF assembly cannot complete."""


@dataclass(frozen=True)
class GifAssemblyOptions:
    duration_ms: int = DEFAULT_DURATION_MS
    loop: int = 0
    optimize: bool = False
    resize: tuple[int, int] | None = None
    max_size: tuple[int | None, int | None] | None = None


@dataclass(frozen=True)
class CursorOverlayFrame:
    """One rendered cursor frame derived from a source PNG frame."""

    source_path: Path
    output_path: Path
    position: tuple[float, float]
    state: str = "default"


@dataclass(frozen=True)
class CursorOverlayOptions:
    cursor_size: int = 28
    click_radius: int = 17


def _natural_sort_key(path: Path) -> tuple[tuple[int, object], ...]:
    parts = re.split(r"(\d+)", path.name.lower())
    return tuple((0, int(part)) if part.isdigit() else (1, part) for part in parts)


def collect_png_frames(frame_dir: Path, pattern: str = DEFAULT_FRAME_PATTERN) -> list[Path]:
    """Return ordered PNG frames from a directory without modifying them."""
    if not frame_dir.exists():
        raise GifAssemblyError(f"Frame directory does not exist: {frame_dir}")
    if not frame_dir.is_dir():
        raise GifAssemblyError(f"Frame path is not a directory: {frame_dir}")

    frame_paths = sorted(frame_dir.glob(pattern), key=_natural_sort_key)
    frame_paths = [path for path in frame_paths if path.is_file()]
    if not frame_paths:
        raise GifAssemblyError(f"No PNG frames matched {pattern!r} in {frame_dir}")
    return frame_paths


def _parse_size(raw_size: str) -> tuple[int, int]:
    match = re.fullmatch(r"\s*(\d+)\s*x\s*(\d+)\s*", raw_size.lower())
    if match is None:
        raise argparse.ArgumentTypeError("Use WIDTHxHEIGHT, for example 960x540")
    width = int(match.group(1))
    height = int(match.group(2))
    if width <= 0 or height <= 0:
        raise argparse.ArgumentTypeError("Width and height must be positive")
    return width, height


def _resolve_target_size(
    original_size: tuple[int, int],
    options: GifAssemblyOptions,
) -> tuple[int, int]:
    if options.resize is not None:
        return options.resize

    if options.max_size is None:
        return original_size

    max_width, max_height = options.max_size
    width, height = original_size
    scale = 1.0
    if max_width is not None and width > max_width:
        scale = min(scale, max_width / width)
    if max_height is not None and height > max_height:
        scale = min(scale, max_height / height)

    if scale >= 1.0:
        return original_size
    return max(1, round(width * scale)), max(1, round(height * scale))


def _open_documentation_frame(path: Path):
    try:
        from PIL import Image
    except ImportError as exc:
        raise GifAssemblyError("Pillow is required to assemble GIFs from PNG frames") from exc

    return Image.open(path).convert("RGBA")


def _draw_cursor_overlay(image, position: tuple[float, float], state: str, options: CursorOverlayOptions) -> None:
    try:
        from PIL import ImageDraw
    except ImportError as exc:
        raise GifAssemblyError("Pillow is required to draw cursor overlays") from exc

    if state not in {"default", "left_click", "right_click", "drag"}:
        raise GifAssemblyError(f"Unknown cursor overlay state: {state}")

    x, y = position
    scale = max(0.5, options.cursor_size / 28.0)
    draw = ImageDraw.Draw(image, "RGBA")

    if state in {"left_click", "right_click"}:
        radius = options.click_radius
        color = (120, 199, 255, 210) if state == "left_click" else (255, 105, 105, 220)
        bbox = [x - radius, y - radius, x + radius, y + radius]
        draw.ellipse(bbox, outline=color, width=max(2, round(3 * scale)))
        inner = max(4, radius // 3)
        draw.ellipse([x - inner, y - inner, x + inner, y + inner], fill=color)

    if state == "drag":
        radius = max(7, round(options.click_radius * 0.55))
        draw.ellipse(
            [x - radius, y - radius, x + radius, y + radius],
            outline=(255, 215, 120, 210),
            width=max(2, round(3 * scale)),
        )

    points = [
        (0.0, 0.0),
        (0.0, 22.0),
        (6.2, 16.7),
        (10.8, 28.0),
        (15.4, 26.1),
        (11.0, 15.4),
        (20.2, 15.4),
    ]
    scaled = [(x + px * scale, y + py * scale) for px, py in points]
    outline = []
    for dx in (-1.4, 1.4):
        for dy in (-1.4, 1.4):
            outline.append([(px + dx * scale, py + dy * scale) for px, py in scaled])
    for polygon in outline:
        draw.polygon(polygon, fill=(0, 0, 0, 230))
    draw.polygon(scaled, fill=(255, 255, 255, 245))
    draw.line(scaled + [scaled[0]], fill=(0, 0, 0, 255), width=max(1, round(1.2 * scale)))


def write_cursor_overlay_frames(
    frame_specs: Sequence[CursorOverlayFrame],
    *,
    options: CursorOverlayOptions | None = None,
) -> list[Path]:
    """Write PNG frames with a synthetic cursor drawn on top of source screenshots."""
    if not frame_specs:
        raise GifAssemblyError("At least one cursor overlay frame is required")

    resolved_options = CursorOverlayOptions() if options is None else options
    if resolved_options.cursor_size <= 0:
        raise GifAssemblyError("Cursor size must be positive")
    if resolved_options.click_radius <= 0:
        raise GifAssemblyError("Click radius must be positive")

    written_paths: list[Path] = []
    for spec in frame_specs:
        image = _open_documentation_frame(Path(spec.source_path))
        try:
            _draw_cursor_overlay(image, spec.position, spec.state, resolved_options)
            output_path = Path(spec.output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            image.save(output_path)
            written_paths.append(output_path)
        finally:
            image.close()
    return written_paths


def assemble_gif_from_frames(
    frame_paths: Sequence[Path],
    output_path: Path,
    options: GifAssemblyOptions | None = None,
) -> Path:
    """Create a GIF from ordered frame paths and leave the source PNGs untouched."""
    if not frame_paths:
        raise GifAssemblyError("At least one PNG frame is required")

    resolved_options = GifAssemblyOptions() if options is None else options
    if resolved_options.duration_ms <= 0:
        raise GifAssemblyError("Frame duration must be positive")
    if resolved_options.loop < 0:
        raise GifAssemblyError("Loop count must be zero or greater")

    images = []
    first_size: tuple[int, int] | None = None
    target_size: tuple[int, int] | None = None

    try:
        for frame_path in frame_paths:
            image = _open_documentation_frame(Path(frame_path))
            if first_size is None:
                first_size = image.size
                target_size = _resolve_target_size(first_size, resolved_options)
            elif resolved_options.resize is None and resolved_options.max_size is None:
                if image.size != first_size:
                    image.close()
                    raise GifAssemblyError(
                        "All GIF frames must have the same size unless --resize, "
                        "--max-width, or --max-height is used"
                    )

            if target_size is not None and image.size != target_size:
                from PIL import Image

                resized_image = image.resize(target_size, Image.Resampling.LANCZOS)
                image.close()
                image = resized_image
            images.append(image)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        first, rest = images[0], images[1:]
        first.save(
            output_path,
            save_all=True,
            append_images=rest,
            duration=resolved_options.duration_ms,
            loop=resolved_options.loop,
            optimize=resolved_options.optimize,
            disposal=2,
        )
    finally:
        for image in images:
            image.close()

    return output_path


def assemble_gif_from_directory(
    frame_dir: Path,
    output_path: Path,
    *,
    pattern: str = DEFAULT_FRAME_PATTERN,
    options: GifAssemblyOptions | None = None,
) -> Path:
    """Create a GIF from a directory of ordered PNG frame files."""
    frame_paths = collect_png_frames(frame_dir, pattern=pattern)
    return assemble_gif_from_frames(frame_paths, output_path, options=options)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Assemble an animated documentation GIF from PNG screenshot frames.",
    )
    parser.add_argument(
        "frame_dir",
        type=Path,
        help="Directory containing ordered PNG frames such as frame-01.png.",
    )
    parser.add_argument(
        "output",
        type=Path,
        help="Output GIF path.",
    )
    parser.add_argument(
        "--pattern",
        default=DEFAULT_FRAME_PATTERN,
        help=f"Frame glob pattern inside frame_dir. Defaults to {DEFAULT_FRAME_PATTERN}.",
    )
    parser.add_argument(
        "--duration-ms",
        type=int,
        default=DEFAULT_DURATION_MS,
        help=f"Stable duration for every frame. Defaults to {DEFAULT_DURATION_MS} ms.",
    )
    parser.add_argument(
        "--loop",
        type=int,
        default=0,
        help="GIF loop count. Use 0 for infinite looping.",
    )
    parser.add_argument(
        "--resize",
        type=_parse_size,
        default=None,
        metavar="WIDTHxHEIGHT",
        help="Resize every frame to an exact output size.",
    )
    parser.add_argument(
        "--max-width",
        type=int,
        default=None,
        help="Scale frames down to this width while preserving the first frame aspect ratio.",
    )
    parser.add_argument(
        "--max-height",
        type=int,
        default=None,
        help="Scale frames down to this height while preserving the first frame aspect ratio.",
    )
    parser.add_argument(
        "--optimize",
        action="store_true",
        help="Ask Pillow to optimize the GIF. This may reduce size but can take longer.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List ordered frames and planned output without writing a GIF.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.max_width is not None and args.max_width <= 0:
        raise SystemExit("--max-width must be positive")
    if args.max_height is not None and args.max_height <= 0:
        raise SystemExit("--max-height must be positive")

    try:
        frame_paths = collect_png_frames(args.frame_dir, pattern=args.pattern)
        options = GifAssemblyOptions(
            duration_ms=args.duration_ms,
            loop=args.loop,
            optimize=args.optimize,
            resize=args.resize,
            max_size=(args.max_width, args.max_height)
            if args.max_width is not None or args.max_height is not None
            else None,
        )

        if args.dry_run:
            print(f"Would assemble {len(frame_paths)} frame(s) into {args.output}:")
            for frame_path in frame_paths:
                print(f"- {frame_path}")
            return 0

        output_path = assemble_gif_from_frames(frame_paths, args.output, options=options)
    except GifAssemblyError as exc:
        raise SystemExit(str(exc)) from exc

    print(f"Wrote animated GIF: {output_path}")
    print(f"Source PNG frames kept in: {args.frame_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
