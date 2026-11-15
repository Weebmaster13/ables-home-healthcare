"""Browser-tab favicon + header logo regression tests (local only)."""
import struct
from pathlib import Path

from app import app

ROOT = Path(__file__).resolve().parent.parent
IMG = ROOT / "static" / "img"


def _png_size(data):
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def test_head_references_all_brand_icons():
    with app.test_client() as client:
        html = client.get("/").data.decode("utf-8")
    assert 'rel="icon" type="image/svg+xml"' in html
    assert "img/logo-circle.svg" in html
    assert 'sizes="32x32"' in html and "favicon-32x32.png" in html
    assert 'sizes="16x16"' in html and "favicon-16x16.png" in html
    assert 'rel="shortcut icon" href="/favicon.ico"' in html
    assert 'rel="apple-touch-icon"' in html
    assert "apple-touch-icon.png" in html


def test_favicon_files_exist_on_disk():
    assert (IMG / "logo-circle.svg").is_file()
    assert (IMG / "favicon-16x16.png").is_file()
    assert (IMG / "favicon-32x32.png").is_file()
    assert (IMG / "apple-touch-icon.png").is_file()
    assert (IMG / "favicon.ico").is_file()
    assert _png_size((IMG / "apple-touch-icon.png").read_bytes()) == (180, 180)
    assert _png_size((IMG / "favicon-32x32.png").read_bytes()) == (32, 32)
    assert _png_size((IMG / "favicon-16x16.png").read_bytes()) == (16, 16)


def test_root_icon_routes_serve_correct_types():
    with app.test_client() as client:
        ico = client.get("/favicon.ico")
        assert ico.status_code == 200
        assert ico.content_type == "image/x-icon"
        assert ico.data[:4] == b"\x00\x00\x01\x00"  # ICO magic
        touch = client.get("/apple-touch-icon.png")
        assert touch.status_code == 200
        assert touch.content_type == "image/png"
        assert _png_size(touch.data) == (180, 180)


def test_static_icon_and_header_logo_paths_resolve():
    with app.test_client() as client:
        for path, ctype, check in [
            ("/static/img/logo-circle.svg", "image/svg+xml", None),
            ("/static/img/favicon-32x32.png", "image/png", (32, 32)),
            ("/static/img/favicon-16x16.png", "image/png", (16, 16)),
            ("/static/img/apple-touch-icon.png", "image/png", (180, 180)),
            ("/static/img/brand/ablelogo-mark.jpg", "image/jpeg", None),
        ]:
            response = client.get(path)
            assert response.status_code == 200, path
            assert response.content_type.split(";")[0] == ctype, path
            if check:
                assert _png_size(response.data) == check, path
        html = client.get("/").data.decode("utf-8")
        assert "ablelogo-mark" in html  # header brand mark (optimized file)
        assert 'alt="Ables Home Healthcare logo"' in html
