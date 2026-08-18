from pathlib import Path
from unittest.mock import Mock

from PIL import Image

from allin1_sdk import branding


ROOT = Path(__file__).resolve().parents[1]


def _favicon(root: Path) -> Path:
    path = root / "assets" / "favicon.ico"
    path.parent.mkdir()
    Image.new("RGBA", (64, 64), (42, 156, 80, 255)).save(path, format="ICO")
    return path


def test_sdk_icon_applies_photo_and_native_bitmap(tmp_path, monkeypatch):
    favicon = _favicon(tmp_path)
    window = Mock()
    photo = object()
    monkeypatch.setattr(branding.ImageTk, "PhotoImage", Mock(return_value=photo))

    applied = branding.apply_sdk_window_icon(window, tmp_path)

    assert applied is True
    window.iconphoto.assert_called_once_with(True, photo)
    assert window._allin1_sdk_icon_photo is photo
    if branding.os.name == "nt":
        window.iconbitmap.assert_called_once_with(default=str(favicon))


def test_sdk_icon_reports_missing_asset(tmp_path):
    window = Mock()

    assert branding.apply_sdk_window_icon(window, tmp_path) is False
    window.iconphoto.assert_not_called()
    window.iconbitmap.assert_not_called()


def test_distributed_favicon_has_small_and_high_dpi_frames():
    with Image.open(ROOT / "assets" / "favicon.ico") as icon:
        assert {(16, 16), (32, 32), (48, 48), (128, 128), (256, 256)} <= \
            icon.ico.sizes()


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
