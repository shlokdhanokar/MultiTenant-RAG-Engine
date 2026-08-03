"""
OCR support for scanned documents and standalone images.

Two entry points into OCR:
  1. A PDF whose pages yield almost no extractable text — that's a scan, and
     without OCR it would ingest as an empty knowledge base with no error.
  2. A directly uploaded image file.

OCR requires the Tesseract binary, which is a system package rather than a
Python dependency. It is therefore treated as optional: if Tesseract is
missing, ingestion degrades to text-only extraction with a warning instead of
failing the whole upload.
"""
import logging

logger = logging.getLogger(__name__)

# Below this many extracted characters per page, a PDF is assumed to be scanned.
SCANNED_PAGE_CHAR_THRESHOLD = 50


def is_ocr_available():
    """True only if both pytesseract and the Tesseract binary are usable."""
    try:
        import pytesseract
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def ocr_image_bytes(image_bytes):
    """Runs OCR over raw image bytes. Returns extracted text, or '' on failure."""
    try:
        import io
        import pytesseract
        from PIL import Image

        image = Image.open(io.BytesIO(image_bytes))
        return (pytesseract.image_to_string(image) or "").strip()
    except Exception as e:
        logger.error(f"OCR failed on image: {e}")
        return ""


def looks_like_scanned_pdf(layout):
    """
    True when a parsed PDF produced far too little text for its page count,
    which indicates page images rather than embedded text.
    """
    if not layout:
        return False
    total_pages = max(layout.get("total_pages", 0), 1)
    total_chars = sum(len(b.get("text", "")) for b in layout.get("semantic_blocks", []))
    return (total_chars / total_pages) < SCANNED_PAGE_CHAR_THRESHOLD


def ocr_pdf_pages(pdf_bytes):
    """
    Renders each PDF page to an image and OCRs it.
    Returns semantic blocks in the same shape the other parsers emit.
    """
    if not is_ocr_available():
        logger.warning("Tesseract not available — skipping OCR for scanned PDF")
        return []

    try:
        import fitz
        document = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as e:
        logger.error(f"OCR could not open PDF: {e}")
        return []

    blocks = []
    for page_index in range(len(document)):
        try:
            # 2x zoom materially improves OCR accuracy over the default 72 DPI.
            pixmap = document[page_index].get_pixmap(matrix=fitz.Matrix(2, 2))
            text = ocr_image_bytes(pixmap.tobytes("png"))
        except Exception as e:
            logger.error(f"OCR failed on page {page_index}: {e}")
            continue

        if not text:
            continue

        blocks.append({
            "page_number": page_index,
            "text": text,
            "font_size": 0,
            "bbox": (0, 0, 0, 0),
            "type": "Topic" if page_index == 0 else "Paragraph",
        })

    document.close()
    logger.info(f"OCR extracted text from {len(blocks)} page(s)")
    return blocks


def analyze_image_layout(image_bytes, filename="image"):
    """Builds a single-block layout from an uploaded image via OCR."""
    if not is_ocr_available():
        logger.warning("Tesseract not available — cannot ingest image uploads")
        return None

    text = ocr_image_bytes(image_bytes)
    if not text:
        return None

    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "png"

    return {
        "total_pages": 1,
        "semantic_blocks": [{
            "page_number": 0,
            "text": text,
            "font_size": 0,
            "bbox": (0, 0, 0, 0),
            "type": "Topic",
        }],
        "images": [{
            "page_number": 0,
            "image_bytes": image_bytes,
            "image_ext": ext,
            "width": 0,
            "height": 0,
            "bbox": (0, 1, 0, 1),
        }],
    }
