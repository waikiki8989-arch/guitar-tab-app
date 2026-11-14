from pathlib import Path
from io import BytesIO

import pytest

import omr
import pdf_preview
import app as app_module
from musicxml_parser import parse_score

fitz = pytest.importorskip("fitz")


def test_png_from_data_url_round_trips():
    raw = b"\x89PNG\r\n\x1a\nexample"
    import base64
    encoded = "data:image/png;base64," + base64.b64encode(raw).decode()
    assert omr.png_from_data_url(encoded) == raw


def test_invalid_image_data_url_is_rejected():
    with pytest.raises(omr.OMRExecutionError):
        omr.png_from_data_url("data:image/jpeg;base64,abc")


def test_missing_omr_command_is_reported(monkeypatch):
    monkeypatch.setenv("GUITAR_TAB_OMR_COMMAND", "/definitely/missing/homr")
    with pytest.raises(omr.OMRExecutionError, match="OMRエンジンが見つかりません"):
        omr.recognize_png(b"\x89PNG\r\n\x1a\nexample")


def test_pdf_page_recognition_renders_parser_result(monkeypatch):
    fixture = Path("tests/fixtures/score_playback.musicxml").read_bytes()
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 100), "test")
    pdf_key, _ = pdf_preview.load(pdf.tobytes(), "楽譜.pdf")
    pdf.close()
    monkeypatch.setattr(app_module.omr, "recognize_png", lambda _: fixture)

    response = app_module.app.test_client().post(
        "/pdf", data={"action": "recognize", "pdf_key": pdf_key, "page": "1"}
    )

    assert response.status_code == 200
    assert "認識結果（1ページ）" in response.get_data(as_text=True)
    assert "音符数" in response.get_data(as_text=True)


def test_pdf_page_generation_uses_shared_solo_pipeline(monkeypatch):
    fixture = Path("tests/fixtures/score_playback.musicxml").read_bytes()
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 100), "test")
    pdf_key, _ = pdf_preview.load(pdf.tobytes(), "楽譜.pdf")
    pdf.close()
    monkeypatch.setattr(app_module.omr, "recognize_png", lambda _: fixture)

    response = app_module.app.test_client().post(
        "/pdf", data={"action": "generate-tab", "pdf_key": pdf_key, "page": "1",
                      "conversion_mode": "auto", "key_mode": "major", "capo": "auto",
                      "max_voices": "3", "max_fret": "20"}
    )

    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "ギターTAB譜（" in html
    assert "認識結果（1ページ）" in html
    assert "五線譜とギターTAB譜" in html
    assert "自動判定（ソロギター）" in html
