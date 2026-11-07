from __future__ import annotations

from dataclasses import dataclass
import base64
import secrets
import time

try:
    import fitz
except ImportError:  # pragma: no cover
    fitz = None

MAX_PDF_BYTES = 16 * 1024 * 1024
MAX_PDF_PAGES = 100
_CACHE = {}


class PdfPreviewError(ValueError):
    pass


@dataclass(frozen=True)
class PdfPage:
    number: int
    width: float
    height: float
    image: str


@dataclass(frozen=True)
class PdfDocument:
    file_name: str
    file_size: int
    pages: tuple[PdfPage, ...]


def _cleanup():
    now = time.monotonic()
    for key, (created, _) in list(_CACHE.items()):
        if now - created > 3600:
            del _CACHE[key]


def get(key):
    _cleanup()
    item = _CACHE.get(key)
    return item[1] if item else None


def load(raw: bytes, file_name: str):
    if fitz is None:
        raise PdfPreviewError("PDF処理ライブラリがありません。PyMuPDFをインストールしてください。")
    if not file_name or not file_name.lower().endswith(".pdf"):
        raise PdfPreviewError("PDFファイルを選択してください。")
    if len(raw) > MAX_PDF_BYTES:
        raise PdfPreviewError("PDFが大きすぎます。16 MiB以下のファイルを選択してください。")
    document = None
    try:
        document = fitz.open(stream=raw, filetype="pdf")
        if document.needs_pass:
            raise PdfPreviewError("パスワード付きPDFは読み込めません。")
        if document.page_count == 0:
            raise PdfPreviewError("空のPDFは読み込めません。")
        if document.page_count > MAX_PDF_PAGES:
            raise PdfPreviewError(f"PDFは{MAX_PDF_PAGES}ページ以下にしてください。")
        pages = []
        for index in range(document.page_count):
            page = document.load_page(index)
            pixmap = page.get_pixmap(dpi=150, alpha=False)
            image = base64.b64encode(pixmap.tobytes("png")).decode("ascii")
            pages.append(PdfPage(index + 1, page.rect.width, page.rect.height,
                                 f"data:image/png;base64,{image}"))
    except PdfPreviewError:
        raise
    except Exception as exc:
        raise PdfPreviewError("PDFを読み込めませんでした。ファイルが破損している可能性があります。") from exc
    finally:
        if document is not None:
            document.close()
    result = PdfDocument(file_name, len(raw), tuple(pages))
    _cleanup()
    key = secrets.token_urlsafe(18)
    _CACHE[key] = (time.monotonic(), result)
    return key, result


def selected_pages(values, page_count):
    pages = []
    for value in values:
        try:
            page = int(value)
        except (TypeError, ValueError) as exc:
            raise PdfPreviewError("認識対象ページの指定が不正です。") from exc
        if not 1 <= page <= page_count:
            raise PdfPreviewError("認識対象ページの指定が不正です。")
        if page not in pages:
            pages.append(page)
    return tuple(sorted(pages)) or tuple(range(1, page_count + 1))
