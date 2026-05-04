import fitz  # PyMuPDF
import io


def load_pdf_document(pdf_bytes: bytes):
    """Loads a PDF from raw bytes in memory (no disk write needed)."""
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        return doc
    except Exception as e:
        print("failed to load pdf from memory:", e)
        return None


def extract_raw_images(doc):
    """
    Extract all images from every page of the PDF.
    """
    images = []
    for page_index in range(len(doc)):
        page = doc[page_index]
        image_list = page.get_images(full=True)

        for img_info in image_list:
            xref = img_info[0]
            try:
                base_image = doc.extract_image(xref)
            except Exception:
                continue
            if not base_image:
                continue

            rects = page.get_image_rects(xref)
            bbox = None
            if rects:
                r = rects[0]
                bbox = (r.x0, r.y0, r.x1, r.y1)

            images.append({
                "page_number": page_index,
                "image_bytes": base_image["image"],
                "image_ext": base_image["ext"],
                "width": base_image.get("width", 0),
                "height": base_image.get("height", 0),
                "bbox": bbox,
            })
    return images


def extract_semantic_text_blocks(doc):
    """
    Extract text not just as a raw string, but as structured blocks (dictionaries)
    that preserve font sizes and styles to identify headers (Topics) vs. body text (Paragraphs).
    """
    raw_blocks = []
    for page_index in range(len(doc)):
        page = doc[page_index]
        page_dict = page.get_text("dict")

        for block in page_dict.get("blocks", []):
            if block.get("type") != 0:
                continue

            block_text_parts = []
            span_sizes = []

            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = span.get("text", "").strip()
                    if text:
                        block_text_parts.append(text)
                        span_sizes.append(span["size"])

            full_text = " ".join(block_text_parts).strip()
            if not full_text:
                continue

            dominant_size = max(set(span_sizes), key=span_sizes.count)

            raw_blocks.append({
                "page_number": page_index,
                "text": full_text,
                "font_size": round(dominant_size, 2),
                "bbox": block.get("bbox", None),
            })
    return raw_blocks


def detect_topic_boundaries(raw_blocks):
    """
    Implement logic to detect when a new "Topic" starts based on header formatting 
    (e.g., larger font sizes) extracted in the previous step.
    """
    if not raw_blocks:
        return []

    # Find the median font size to establish the baseline for "body text"
    all_sizes = sorted([b["font_size"] for b in raw_blocks])
    mid = len(all_sizes) // 2
    median_size = (all_sizes[mid - 1] + all_sizes[mid]) / 2 if len(all_sizes) % 2 == 0 else all_sizes[mid]

    # Threshold: noticeably larger than median is a Topic (header)
    topic_threshold = median_size * 1.15

    tagged_blocks = []
    for block in raw_blocks:
        block_type = "Topic" if block["font_size"] >= topic_threshold else "Paragraph"
        block["type"] = block_type
        tagged_blocks.append(block)

    return tagged_blocks


def analyze_document_layout(pdf_bytes: bytes):
    """
    Master function that orchestrates layout extraction.
    """
    doc = load_pdf_document(pdf_bytes)
    if doc is None:
        return None

    raw_blocks = extract_semantic_text_blocks(doc)
    tagged_blocks = detect_topic_boundaries(raw_blocks)
    images = extract_raw_images(doc)

    result = {
        "total_pages": len(doc),
        "semantic_blocks": tagged_blocks,
        "images": images,
    }

    doc.close()
    return result
