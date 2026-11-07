from io import BytesIO
from pathlib import Path

import pytest

fitz = pytest.importorskip("fitz")

import pdf_preview
from app import app


def make_pdf(page_count=2):
    document = fitz.open()
    for number in range(page_count):
        page = document.new_page(width=595, height=842)
        page.insert_text((72, 100), f"Test page {number + 1}")
    raw = document.tobytes()
    document.close()
    return raw


def test_load_returns_page_images_and_metadata():
    key, document = pdf_preview.load(make_pdf(2), "楽譜_日本語.pdf")

    assert pdf_preview.get(key) == document
    assert document.file_name == "楽譜_日本語.pdf"
    assert len(document.pages) == 2
    assert document.pages[0].image.startswith("data:image/png;base64,")
    assert document.pages[0].width == pytest.approx(595)


@pytest.mark.parametrize("file_name", ["score.txt", "score.musicxml", "score"])
def test_non_pdf_is_rejected(file_name):
    with pytest.raises(pdf_preview.PdfPreviewError, match="PDFファイル"):
        pdf_preview.load(make_pdf(), file_name)


def test_invalid_pdf_is_rejected():
    with pytest.raises(pdf_preview.PdfPreviewError, match="PDFを読み込めませんでした"):
        pdf_preview.load(b"not a pdf", "broken.pdf")


def test_selected_pages_are_validated_and_sorted():
    assert pdf_preview.selected_pages(["3", "1", "3"], 3) == (1, 3)
    assert pdf_preview.selected_pages([], 3) == (1, 2, 3)
    with pytest.raises(pdf_preview.PdfPreviewError):
        pdf_preview.selected_pages(["0"], 3)
    with pytest.raises(pdf_preview.PdfPreviewError):
        pdf_preview.selected_pages(["four"], 3)


def test_pdf_upload_page_accepts_japanese_file_name():
    client = app.test_client()
    response = client.post(
        "/pdf",
        data={"action": "upload", "pdf_file": (BytesIO(make_pdf()), "楽譜_日本語.pdf")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    assert "楽譜_日本語.pdf" in response.get_data(as_text=True)
    assert "2ページ" in response.get_data(as_text=True)


def test_pdf_upload_rejects_non_pdf():
    client = app.test_client()
    response = client.post(
        "/pdf",
        data={"action": "upload", "pdf_file": (BytesIO(b"text"), "score.txt")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    assert "PDFファイルを選択してください" in response.get_data(as_text=True)


def test_manual_pdf_fixture_is_usable_when_present():
    path = Path("tests/manual_data/pdf/kokomu_033898_[シータピアノ] ヨルシカ - 都落ち.pdf")
    if not path.exists():
        pytest.skip("手動検証用PDFがありません")
    _, document = pdf_preview.load(path.read_bytes(), path.name)
    assert len(document.pages) == 5
    assert all(page.image.startswith("data:image/png;base64,") for page in document.pages)
