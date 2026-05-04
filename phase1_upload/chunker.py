def group_content_by_topic(tagged_blocks):
    """
    Aggregate all paragraph text that falls under a specific Topic header into a 
    single, cohesive semantic unit before applying length constraints.
    """
    topic_groups = []
    current_topic = None
    current_paragraphs = []
    
    for block in tagged_blocks:
        if block["type"] == "Topic":
            # If we hit a new topic, save the old one and start fresh
            if current_paragraphs or current_topic:
                topic_groups.append({
                    "topic": current_topic,
                    "paragraphs": current_paragraphs
                })
            current_topic = block["text"]
            current_paragraphs = []
        else:
            # It's a paragraph
            current_paragraphs.append({
                "text": block["text"],
                "page_number": block.get("page_number", 0),
                "bbox": block.get("bbox")
            })

    # Flush the last topic
    if current_paragraphs or current_topic:
        topic_groups.append({
            "topic": current_topic,
            "paragraphs": current_paragraphs
        })

    return topic_groups


def preserve_semantic_integrity(current_words, next_para_words, max_words=300):
    """
    If a paragraph ends at word 190, the chunker intelligently breaks at the 
    paragraph boundary rather than cutting off mid-sentence to reach the limit.
    Returns True if we should break the chunk NOW (before adding the next paragraph).
    """
    if current_words == 0:
        return False  # Never break an empty chunk
    
    # If adding this paragraph pushes us over the limit, seal the current chunk
    if current_words + next_para_words > max_words:
        return True
        
    return False


def enforce_overlap_constraints(previous_chunk_text, overlap_words=50):
    """
    Ensure that each 200-300 word chunk has a configurable overlap (e.g., 50 words) 
    with the previous chunk so that context is not lost between chunk boundaries.
    """
    if not previous_chunk_text:
        return ""
    
    words = previous_chunk_text.split()
    if len(words) <= overlap_words:
        return previous_chunk_text
        
    overlap_segment = " ".join(words[-overlap_words:])
    return overlap_segment


def generate_semantic_chunks(topic_groups, min_words=200, max_words=300, overlap_words=50):
    """
    Take the grouped text and apply a sliding window to split it into chunks 
    of exactly 200 to 300 words, enforcing overlap and semantic integrity.
    Also tracks the physical bounding box (y-coordinates) of the chunk per page.
    """
    chunks = []
    
    for group in topic_groups:
        topic = group["topic"]
        paragraphs = group["paragraphs"]
        
        current_chunk_text = ""
        current_chunk_words = 0
        current_page_start = paragraphs[0]["page_number"] if paragraphs else 0
        current_page_end = current_page_start
        current_page_bboxes = {}  # Map of page_number -> {"y0": min_y, "y1": max_y}
        
        def update_bboxes(para):
            nonlocal current_page_bboxes
            p_num = para["page_number"]
            bbox = para.get("bbox")
            if not bbox:
                return
            x0, y0, x1, y1 = bbox
            if p_num not in current_page_bboxes:
                current_page_bboxes[p_num] = {"y0": y0, "y1": y1}
            else:
                current_page_bboxes[p_num]["y0"] = min(current_page_bboxes[p_num]["y0"], y0)
                current_page_bboxes[p_num]["y1"] = max(current_page_bboxes[p_num]["y1"], y1)

        for para in paragraphs:
            para_text = para["text"].strip()
            if not para_text:
                continue
                
            para_words = len(para_text.split())
            
            should_break = preserve_semantic_integrity(current_chunk_words, para_words, max_words)
            if not should_break and current_chunk_words >= min_words:
                should_break = True

            if should_break:
                chunks.append({
                    "chunk_index": len(chunks),
                    "header": topic,
                    "text": current_chunk_text.strip(),
                    "word_count": current_chunk_words,
                    "page_start": current_page_start,
                    "page_end": current_page_end,
                    "page_bboxes": current_page_bboxes
                })
                
                overlap_text = enforce_overlap_constraints(current_chunk_text, overlap_words)
                current_chunk_text = overlap_text + " " + para_text
                current_chunk_words = len(current_chunk_text.split())
                current_page_start = para["page_number"]
                current_page_end = para["page_number"]
                current_page_bboxes = {}
                update_bboxes(para)
            else:
                current_chunk_text += (" " + para_text if current_chunk_text else para_text)
                current_chunk_words += para_words
                current_page_end = para["page_number"]
                update_bboxes(para)

        if current_chunk_words > 0:
            chunks.append({
                "chunk_index": len(chunks),
                "header": topic,
                "text": current_chunk_text.strip(),
                "word_count": current_chunk_words,
                "page_start": current_page_start,
                "page_end": current_page_end,
                "page_bboxes": current_page_bboxes
            })

    return chunks


def map_images_to_chunks(chunks, uploaded_images):
    """
    Cross-reference text chunks with extracted images using spatial bounding boxes.
    An image is associated with a chunk if its vertical center falls within 
    (or close to) the text chunk's vertical boundaries on that specific page.

    Args:
        chunks: list of chunk dicts from generate_semantic_chunks()
        uploaded_images: list of dicts with {page_number, gridfs_id, bbox}

    Returns:
        The enriched chunks list with associated_image_ids added to each chunk.
    """
    for chunk in chunks:
        associated_ids = []
        page_bboxes = chunk.get("page_bboxes", {})
        
        for img in uploaded_images:
            img_page = img["page_number"]
            img_bbox = img.get("bbox")
            
            # If the chunk doesn't have text on the image's page, skip
            if img_page not in page_bboxes:
                continue
                
            # If image has no bbox, fallback to pure page mapping (rare)
            if not img_bbox:
                associated_ids.append(img["gridfs_id"])
                continue
                
            # Spatial check
            chunk_y0 = page_bboxes[img_page]["y0"]
            chunk_y1 = page_bboxes[img_page]["y1"]
            
            img_y0 = img_bbox[1] # bbox is (x0, y0, x1, y1)
            img_y1 = img_bbox[3]
            img_center_y = (img_y0 + img_y1) / 2
            
            # Allow a 50 pixel margin of error above and below the text chunk
            margin = 50
            if (chunk_y0 - margin) <= img_center_y <= (chunk_y1 + margin):
                associated_ids.append(img["gridfs_id"])
                
        chunk["associated_image_ids"] = associated_ids

    return chunks
