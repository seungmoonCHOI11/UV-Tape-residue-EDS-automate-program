from pathlib import Path

import pymupdf as fitz

from app.analysis import _make_point_mini_pdf, trim_process_memory


def _make_pdf(path: Path, pages: int = 8):
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page(width=300, height=200)
        page.insert_text((40, 80), f"source-page-{i+1}")
    doc.save(path)
    doc.close()


def test_point_mini_pdf_contains_only_requested_pages(tmp_path):
    src = tmp_path / "large.pdf"
    dst = tmp_path / "point.pdf"
    _make_pdf(src, 8)
    group = [(str(src), 3), (str(src), 4), (str(src), 5)]
    _make_point_mini_pdf(group, dst)
    assert dst.exists()
    with fitz.open(dst) as doc:
        assert len(doc) == 3
        texts = [doc.load_page(i).get_text("text") for i in range(3)]
    assert "source-page-4" in texts[0]
    assert "source-page-5" in texts[1]
    assert "source-page-6" in texts[2]


def test_point_mini_pdf_can_join_pages_from_multiple_sources(tmp_path):
    a = tmp_path / "a.pdf"
    b = tmp_path / "b.pdf"
    dst = tmp_path / "joined.pdf"
    _make_pdf(a, 2)
    _make_pdf(b, 2)
    _make_point_mini_pdf([(str(a), 1), (str(b), 0), (str(b), 1)], dst)
    with fitz.open(dst) as doc:
        assert len(doc) == 3
        texts = [doc.load_page(i).get_text("text") for i in range(3)]
    assert "source-page-2" in texts[0]
    assert "source-page-1" in texts[1]
    assert "source-page-2" in texts[2]


def test_native_memory_trim_is_best_effort_and_never_raises():
    # Cross-platform helper must be safe even where libc malloc_trim is absent.
    trim_process_memory()
