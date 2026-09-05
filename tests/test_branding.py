from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]


def test_distributed_favicon_has_small_and_high_dpi_frames():
    with Image.open(ROOT / "assets" / "favicon.ico") as icon:
        expected = {
            (size, size) for size in (
                16, 20, 24, 28, 32, 36, 40, 48,
                56, 64, 72, 80, 96, 128, 256,
            )
        }
        assert expected == icon.ico.sizes()
        for size in expected:
            frame = icon.ico.getimage(size).convert("RGBA")
            assert frame.getchannel("A").getextrema() == (0, 255)


def test_distributed_sdk_logo_has_real_transparency():
    with Image.open(ROOT / "assets" / "ALLIN1_SDK.png") as logo:
        assert logo.mode == "RGBA"
        assert logo.getchannel("A").getextrema() == (0, 255)
        corners = (
            (0, 0),
            (logo.width - 1, 0),
            (0, logo.height - 1),
            (logo.width - 1, logo.height - 1),
        )
        assert all(logo.getpixel(point)[3] == 0 for point in corners)


def test_compact_title_bar_logo_has_real_transparency():
    with Image.open(ROOT / "assets" / "ALLIN1-icon.png") as logo:
        assert logo.mode == "RGBA"
        assert logo.size == (256, 256)
        assert logo.getchannel("A").getextrema() == (0, 255)
