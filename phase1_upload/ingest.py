"""
Format dispatcher for document ingestion.

Every parser normalizes to the same layout shape:
    {"total_pages": int, "semantic_blocks": [...], "images": [...]}

so everything downstream — topic grouping, semantic chunking, image anchoring,
embedding, storage — stays format-agnostic. Adding a new file type means adding
a parser and one dispatch entry, not touching the pipeline.
"""
import logging

logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "tiff", "tif", "bmp", "webp"}

SUPPORTED_EXTENSIONS = {"pdf", "docx", "pptx", "xlsx"} | IMAGE_EXTENSIONS


class UnsupportedFileTypeError(Exception):
    """Raised when an uploaded file's extension has no registered parser."""


def get_extension(filename):
    return filename.rsplit(".", 1)[-1].lower() if "." in (filename or "") else ""


def analyze_document(file_bytes, filename):
    """
    Routes an uploaded file to the correct parser based on its extension.

    Raises UnsupportedFileTypeError for unknown types; returns None when the
    correct parser was found but the file could not be read.
    """
    ext = get_extension(filename)

    if ext == "pdf":
        from phase1_upload.pdf_parser import analyze_document_layout
        from phase1_upload.ocr import looks_like_scanned_pdf, ocr_pdf_pages

        layout = analyze_document_layout(file_bytes)

        # A scanned PDF parses "successfully" but yields near-zero text. Without
        # this fallback it would silently ingest as an empty knowledge base.
        if layout is not None and looks_like_scanned_pdf(layout):
            logger.info("PDF appears to be scanned — attempting OCR")
            ocr_blocks = ocr_pdf_pages(file_bytes)
            if ocr_blocks:
                layout["semantic_blocks"] = ocr_blocks
        return layout

    if ext == "docx":
        from phase1_upload.docx_parser import analyze_docx_layout
        return analyze_docx_layout(file_bytes)

    if ext == "pptx":
        from phase1_upload.pptx_parser import analyze_pptx_layout
        return analyze_pptx_layout(file_bytes)

    if ext == "xlsx":
        from phase1_upload.xlsx_parser import analyze_xlsx_layout
        return analyze_xlsx_layout(file_bytes)

    if ext in IMAGE_EXTENSIONS:
        from phase1_upload.ocr import analyze_image_layout
        return analyze_image_layout(file_bytes, filename)

    raise UnsupportedFileTypeError(
        f"Unsupported file type '.{ext}'. Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
    )
