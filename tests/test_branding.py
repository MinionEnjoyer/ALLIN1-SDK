from pathlib import Path
from unittest.mock import Mock

from PIL import Image

from allin1_sdk import branding


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
