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
    native = Mock(return_value=True)
    monkeypatch.setattr(branding, "_apply_windows_native_icon", native)

    applied = branding.apply_sdk_window_icon(window, tmp_path)

    assert applied is True
    window.iconphoto.assert_called_once_with(True, photo)
    assert window._allin1_sdk_icon_photo is photo
    if branding.os.name == "nt":
        window.iconbitmap.assert_called_once_with(default=str(favicon))
        native.assert_called_once_with(window, favicon)
    window.after_idle.assert_called_once()


def test_sdk_icon_reapplies_after_native_window_is_mapped(tmp_path, monkeypatch):
    favicon = _favicon(tmp_path)
    window = Mock()
    window.winfo_exists.return_value = True
    native = Mock(return_value=True)
    monkeypatch.setattr(branding, "_apply_windows_native_icon", native)

    branding._reapply_mapped_icon(window, favicon)

    if branding.os.name == "nt":
        window.iconbitmap.assert_called_once_with(default=str(favicon))
    native.assert_called_once_with(window, favicon)


def test_sdk_icon_reports_missing_asset(tmp_path):
    window = Mock()

    assert branding.apply_sdk_window_icon(window, tmp_path) is False
    window.iconphoto.assert_not_called()
    window.iconbitmap.assert_not_called()


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


def test_sdk_workspace_banner_uses_sdk_specific_artwork():
    source = (ROOT / "src" / "allin1_sdk" / "addon_sdk_ui.py").read_text(
        encoding="utf-8"
    )
    assert 'logo = self.project_root / "assets" / "ALLIN1_SDK.png"' in source
    assert 'header_text, text="Developer Workspace"' in source
