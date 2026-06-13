"""PDF to HTML5 conversion for Agenten."""

import os
import re

from pdfminer.high_level import extract_pages, extract_text
from pdfminer.layout import LTTextBox, LTTextLine, LTFigure, LAParams
from pdfminer.pdfpage import PDFPage
from pdfminer.pdfparser import PDFParser
from pdfminer.pdfdocument import PDFDocument


def _get_page_count(pdf_path: str) -> int:
    """Count pages in a PDF file."""
    count = 0
    with open(pdf_path, 'rb') as f:
        parser = PDFParser(f)
        doc = PDFDocument(parser)
        for _ in PDFPage.create_pages(doc):
            count += 1
    return count


def _resolve_pdf_path(path: str) -> str:
    """Resolve a PDF path relative to AGENT_WORKDIR or CWD.

    Handles Windows relative paths like ``docs\\file.pdf``,
    ``.\\docs\\file.pdf``, or ``\\docs\\file.pdf``
    (``\\docs`` is root-relative on Windows but the user means
    relative to workdir).
    """
    path = os.path.normpath(path)
    if os.path.isabs(path):
        if path.startswith("\\") and not path.startswith("\\\\"):
            workdir = os.environ.get("AGENT_WORKDIR", "")
            base = os.path.abspath(workdir) if workdir else os.path.abspath(".")
            return os.path.normpath(os.path.join(base, path.lstrip("\\")))
        return path
    if path.startswith("\\") and not path.startswith("\\\\"):
        path = path.lstrip("\\")
    workdir = os.environ.get("AGENT_WORKDIR", "")
    base = os.path.abspath(workdir) if workdir else os.path.abspath(".")
    return os.path.normpath(os.path.join(base, path))


def _extract_page_text(pdf_path: str) -> list[str]:
    """Extract text per page from a PDF using pdfminer layout-aware extraction.

    Returns a list of strings, one per page, preserving paragraph structure.
    """
    laparams = LAParams(
        all_texts=True,
        detect_vertical=True,
        word_margin=0.1,
        char_margin=2.0,
        line_margin=0.5,
        boxes_flow=0.5,
    )
    pages_text = []
    for page_layout in extract_pages(pdf_path, laparams=laparams):
        page_lines = []
        for element in page_layout:
            if isinstance(element, (LTTextBox, LTTextLine)):
                text = element.get_text().strip()
                if text:
                    page_lines.append(text)
            elif isinstance(element, LTFigure):
                for child in element:
                    if isinstance(child, (LTTextBox, LTTextLine)):
                        text = child.get_text().strip()
                        if text:
                            page_lines.append(text)
        pages_text.append("\n".join(page_lines))
    return pages_text


def convert_pdf_to_html5(pdf_path: str, output_path: str | None = None, lang: str = "da") -> dict:
    """Convert a PDF file to HTML5.

    Extracts text per page using pdfminer and builds clean flow-based HTML5
    with proper paragraphs, page breaks, and responsive styling.

    Args:
        pdf_path: Path to the PDF file
        output_path: Optional output path for the HTML file
        lang: Language code for the HTML lang attribute

    Returns:
        dict with ``success``, ``result`` (filepath), or ``error``
    """
    pdf_path = _resolve_pdf_path(pdf_path)
    if not os.path.exists(pdf_path):
        return {"success": False, "error": f"File not found: {pdf_path}"}

    if not pdf_path.lower().endswith('.pdf'):
        return {"success": False, "error": f"Not a PDF file: {pdf_path}"}

    if output_path is None:
        output_path = os.path.splitext(pdf_path)[0] + '.html'
    else:
        output_path = _resolve_pdf_path(output_path)

    try:
        page_count = _get_page_count(pdf_path)
        pages_text = _extract_page_text(pdf_path)

        body_parts = []
        for i, text in enumerate(pages_text):
            body_parts.append(f'<section class="page" id="page-{i + 1}">')
            body_parts.append(f'<div class="page-number">Side {i + 1}</div>')
            for paragraph in text.split("\n\n"):
                p = paragraph.strip()
                if p:
                    p = _html_escape(p)
                    p = _link_urls(p)
                    body_parts.append(f"<p>{p}</p>")
            body_parts.append("</section>")

        body_html = "\n".join(body_parts)

        title = os.path.basename(pdf_path)
        css = _build_css()
        html = f"""<!DOCTYPE html>
<html lang="{lang}">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
{css}
</style>
</head>
<body>
<header>
<h1>{title}</h1>
<p class="meta">{page_count} side{'' if page_count == 1 else 'r'}</p>
</header>
{body_html}
</body>
</html>"""

        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(html)

        return {"success": True, "result": os.path.abspath(output_path)}

    except Exception as e:
        return {"success": False, "error": str(e)}


def _html_escape(text: str) -> str:
    """Escape HTML special characters and normalize whitespace."""
    text = text.replace("\xa0", " ")
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _link_urls(text: str) -> str:
    """Convert URLs in text to clickable HTML links."""
    url_re = re.compile(r'(https?://[^\s]+)')
    return url_re.sub(r'<a href="\1">\1</a>', text)


def _build_css() -> str:
    """Build clean CSS for the HTML5 output."""
    return """body {
    font-family: Georgia, 'Times New Roman', serif;
    max-width: 800px;
    margin: 0 auto;
    padding: 20px;
    line-height: 1.7;
    color: #1a1a1a;
    background: #fff;
    font-size: 14px;
}
header {
    border-bottom: 2px solid #333;
    margin-bottom: 1.5em;
    padding-bottom: 0.5em;
}
h1 {
    font-size: 1.4em;
    margin: 0;
}
.meta {
    font-size: 0.85em;
    color: #666;
    margin: 0.3em 0 0;
}
.page {
    margin-bottom: 2em;
    page-break-inside: avoid;
}
.page-number {
    font-size: 0.8em;
    color: #999;
    text-align: center;
    border-top: 1px solid #ddd;
    padding-top: 0.5em;
    margin-bottom: 1em;
}
p {
    margin: 0.5em 0;
    text-align: justify;
}
a { color: #2563eb; }
@media print {
    .page { page-break-after: always; }
}
@media (max-width: 600px) {
    body { padding: 10px; font-size: 13px; }
}"""
