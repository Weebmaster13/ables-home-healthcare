"""Optimized media regression tests: new files exist, originals kept."""
from pathlib import Path

from app import app

ROOT = Path(__file__).resolve().parent.parent
IMG = ROOT / "static" / "img"


def test_optimized_files_exist_and_originals_kept():
    assert (IMG / "care" / "hero-family-room-1280.jpg").is_file()
    assert (IMG / "brand" / "ablelogo-mark-256.jpg").is_file()
    # originals must remain on disk (rollback + other references)
    assert (IMG / "care" / "hero-family-room-v2.png").is_file()
    assert (IMG / "brand" / "ablelogo-mark.jpg").is_file()
    assert (IMG / "care" / "hero-family-room-1280.jpg").stat().st_size < 200000
    assert (IMG / "brand" / "ablelogo-mark-256.jpg").stat().st_size < 50000


def test_templates_reference_optimized_images():
    home = (ROOT / "templates" / "home.html").read_text(encoding="utf-8")
    assert "hero-family-room-1280.jpg" in home
    base = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
    assert "ablelogo-mark-256.jpg" in base
    assert 'alt="Ables Home Healthcare logo"' in base


def test_optimized_images_serve_locally():
    with app.test_client() as client:
        for path in ["/static/img/care/hero-family-room-1280.jpg",
                     "/static/img/brand/ablelogo-mark-256.jpg"]:
            response = client.get(path)
            assert response.status_code == 200, path
            assert response.content_type == "image/jpeg", path
        html = client.get("/").data.decode("utf-8")
        assert "hero-family-room-1280.jpg" in html
        assert "ablelogo-mark-256.jpg" in html
