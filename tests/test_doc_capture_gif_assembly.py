from pathlib import Path

import pytest

from scripts.doc_capture.assemble_gif import (
    CursorOverlayFrame,
    CursorOverlayOptions,
    GifAssemblyError,
    GifAssemblyOptions,
    assemble_gif_from_directory,
    collect_png_frames,
    write_cursor_overlay_frames,
)


Image = pytest.importorskip("PIL.Image")


def _write_frame(path: Path, size: tuple[int, int], color: tuple[int, int, int]) -> None:
    Image.new("RGB", size, color).save(path)


def test_collect_png_frames_uses_natural_filename_order(tmp_path):
    frame_dir = tmp_path / "frames"
    frame_dir.mkdir()
    _write_frame(frame_dir / "frame-10.png", (10, 8), (255, 0, 0))
    _write_frame(frame_dir / "frame-02.png", (10, 8), (0, 255, 0))
    _write_frame(frame_dir / "frame-01.png", (10, 8), (0, 0, 255))

    frames = collect_png_frames(frame_dir)

    assert [frame.name for frame in frames] == ["frame-01.png", "frame-02.png", "frame-10.png"]


def test_assemble_gif_from_directory_keeps_source_frames_and_writes_stable_duration(tmp_path):
    frame_dir = tmp_path / "frames"
    frame_dir.mkdir()
    _write_frame(frame_dir / "frame-01.png", (10, 8), (255, 0, 0))
    _write_frame(frame_dir / "frame-02.png", (10, 8), (0, 255, 0))
    output_path = tmp_path / "workflow.gif"

    assemble_gif_from_directory(
        frame_dir,
        output_path,
        options=GifAssemblyOptions(duration_ms=120),
    )

    assert output_path.exists()
    assert sorted(frame.name for frame in frame_dir.glob("*.png")) == ["frame-01.png", "frame-02.png"]
    with Image.open(output_path) as gif:
        assert gif.n_frames == 2
        assert gif.info["duration"] == 120
        assert gif.size == (10, 8)


def test_assemble_gif_can_resize_output_for_docs(tmp_path):
    frame_dir = tmp_path / "frames"
    frame_dir.mkdir()
    _write_frame(frame_dir / "frame-01.png", (20, 10), (255, 0, 0))
    _write_frame(frame_dir / "frame-02.png", (20, 10), (0, 255, 0))
    output_path = tmp_path / "workflow.gif"

    assemble_gif_from_directory(
        frame_dir,
        output_path,
        options=GifAssemblyOptions(resize=(8, 4), optimize=True),
    )

    with Image.open(output_path) as gif:
        assert gif.size == (8, 4)


def test_assemble_gif_can_limit_max_size_for_docs(tmp_path):
    frame_dir = tmp_path / "frames"
    frame_dir.mkdir()
    _write_frame(frame_dir / "frame-01.png", (20, 10), (255, 0, 0))
    _write_frame(frame_dir / "frame-02.png", (20, 10), (0, 255, 0))
    output_path = tmp_path / "workflow.gif"

    assemble_gif_from_directory(
        frame_dir,
        output_path,
        options=GifAssemblyOptions(max_size=(10, None)),
    )

    with Image.open(output_path) as gif:
        assert gif.size == (10, 5)


def test_assemble_gif_rejects_mismatched_frame_sizes_without_resize(tmp_path):
    frame_dir = tmp_path / "frames"
    frame_dir.mkdir()
    _write_frame(frame_dir / "frame-01.png", (20, 10), (255, 0, 0))
    _write_frame(frame_dir / "frame-02.png", (12, 10), (0, 255, 0))

    with pytest.raises(GifAssemblyError, match="same size"):
        assemble_gif_from_directory(frame_dir, tmp_path / "workflow.gif")


def test_write_cursor_overlay_frames_draws_cursor_without_touching_source(tmp_path):
    source_path = tmp_path / "source.png"
    output_path = tmp_path / "frame-01.png"
    _write_frame(source_path, (80, 60), (20, 20, 20))

    written = write_cursor_overlay_frames(
        [
            CursorOverlayFrame(
                source_path=source_path,
                output_path=output_path,
                position=(30, 20),
                state="left_click",
            )
        ],
        options=CursorOverlayOptions(cursor_size=24, click_radius=12),
    )

    assert written == [output_path]
    with Image.open(source_path) as source:
        assert source.getpixel((30, 20)) == (20, 20, 20)
    with Image.open(output_path) as output:
        assert output.size == (80, 60)
        assert output.convert("RGB").getpixel((30, 20)) != (20, 20, 20)


def test_write_cursor_overlay_frames_rejects_unknown_cursor_state(tmp_path):
    source_path = tmp_path / "source.png"
    _write_frame(source_path, (80, 60), (20, 20, 20))

    with pytest.raises(GifAssemblyError, match="Unknown cursor overlay state"):
        write_cursor_overlay_frames(
            [
                CursorOverlayFrame(
                    source_path=source_path,
                    output_path=tmp_path / "frame-01.png",
                    position=(30, 20),
                    state="middle_click",
                )
            ],
        )
