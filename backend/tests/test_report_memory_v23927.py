from pathlib import Path
import tempfile
import pymupdf as fitz
from app.report_merge import merge_documents

def _one_page_pdf(path: Path, text: str):
    doc=fitz.open(); page=doc.new_page(width=200,height=100); page.insert_text((20,50),text); doc.save(path); doc.close()

def test_incremental_pdf_merge_many_parts():
    with tempfile.TemporaryDirectory() as td:
        root=Path(td); parts=[]
        for i in range(37):
            p=root/f'p{i}.pdf'; _one_page_pdf(p,str(i)); parts.append(p)
        out=root/'merged.pdf'
        merge_documents(parts,out,'pdf')
        with fitz.open(out) as doc: assert len(doc)==37

def test_point_batch_limits_are_bounded():
    src=Path(__file__).parents[1]/'app'/'research_reports.py'
    text=src.read_text(encoding='utf8')
    assert "max_pages = 3 if opts.format=='pptx' else 4" in text
