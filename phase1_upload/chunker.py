def group_content_by_topic(tagged_blocks):
    """
    Aggregate all paragraph text into hierarchical topics (Heading + Subheading).
    """
    topic_groups = []
    current_main_topic = "Introduction"
    current_sub_topic = ""
    current_paragraphs = []
    current_heading_pos = {"page": 1, "y0": 0}
    
    for block in tagged_blocks:
        if block["type"] in ["Topic", "Subtopic"]:
            # Save current group if it has text
            if current_paragraphs:
                combined_header = f"{current_main_topic} - {current_sub_topic}" if current_sub_topic else current_main_topic
                topic_groups.append({
                    "topic": combined_header,
                    "paragraphs": current_paragraphs,
                    "heading_pos": current_heading_pos
                })
            
            if block["type"] == "Topic":
                current_main_topic = block["text"]
                current_sub_topic = ""
            else:
                current_sub_topic = block["text"]
                
            current_paragraphs = []
            current_heading_pos = {
                "page": block.get("page_number", 1),
                "y0": block.get("bbox", [0, 0, 0, 0])[1]
            }
        else:
            current_paragraphs.append({
                "text": block["text"],
                "page_number": block.get("page_number", 0),
                "bbox": block.get("bbox")
            })

    if current_paragraphs:
        combined_header = f"{current_main_topic} - {current_sub_topic}" if current_sub_topic else current_main_topic
        topic_groups.append({
            "topic": combined_header,
            "paragraphs": current_paragraphs,
            "heading_pos": current_heading_pos
        })

    return topic_groups


def preserve_semantic_integrity(current_words, next_para_words, max_words=300):
    if current_words == 0: return False
    return (current_words + next_para_words > max_words)


def enforce_overlap_constraints(previous_chunk_text, overlap_words=50):
    if not previous_chunk_text: return ""
    words = previous_chunk_text.split()
    if len(words) <= overlap_words: return previous_chunk_text
    return " ".join(words[-overlap_words:])


def generate_semantic_chunks(topic_groups, min_words=200, max_words=300, overlap_words=50):
    chunks = []
    for group in topic_groups:
        topic = group["topic"]
        heading_pos = group.get("heading_pos", {"page": 1, "y0": 0})
        paragraphs = group["paragraphs"]
        
        if not paragraphs: continue
            
        current_chunk_text = ""
        current_chunk_words = 0
        current_page_start = paragraphs[0]["page_number"]
        current_page_end = current_page_start
        current_page_bboxes = {}
        
        def update_bboxes(para):
            nonlocal current_page_bboxes
            p_num = para["page_number"]; bbox = para.get("bbox")
            if not bbox: return
            y0, y1 = bbox[1], bbox[3]
            if p_num not in current_page_bboxes:
                current_page_bboxes[p_num] = {"y0": y0, "y1": y1}
            else:
                current_page_bboxes[p_num]["y0"] = min(current_page_bboxes[p_num]["y0"], y0)
                current_page_bboxes[p_num]["y1"] = max(current_page_bboxes[p_num]["y1"], y1)

        for para in paragraphs:
            para_text = para["text"].strip()
            if not para_text: continue
            para_words = len(para_text.split())
            
            should_break = preserve_semantic_integrity(current_chunk_words, para_words, max_words)
            if not should_break and current_chunk_words >= min_words: should_break = True

            if should_break:
                chunks.append({
                    "chunk_index": len(chunks),
                    "header": topic,
                    "text": current_chunk_text.strip(),
                    "word_count": current_chunk_words,
                    "page_start": current_page_start,
                    "page_end": current_page_end,
                    "page_bboxes": current_page_bboxes,
                    "heading_pos": heading_pos
                })
                overlap_text = enforce_overlap_constraints(current_chunk_text, overlap_words)
                current_chunk_text = overlap_text + " " + para_text
                current_chunk_words = len(current_chunk_text.split())
                current_page_start = para["page_number"]; current_page_end = para["page_number"]
                current_page_bboxes = {}; update_bboxes(para)
            else:
                current_chunk_text += (" " + para_text if current_chunk_text else para_text)
                current_chunk_words += para_words
                current_page_end = para["page_number"]; update_bboxes(para)

        if current_chunk_words > 0:
            chunks.append({
                "chunk_index": len(chunks),
                "header": topic,
                "text": current_chunk_text.strip(),
                "word_count": current_chunk_words,
                "page_start": current_page_start,
                "page_end": current_page_end,
                "page_bboxes": current_page_bboxes,
                "heading_pos": heading_pos
            })
    return chunks


def map_images_to_chunks(chunks, uploaded_images):
    """
    Associate images with chunks based on PHYSICAL document flow.
    """
    if not uploaded_images: return chunks

    # 1. Collect unique headings and their start positions
    all_headings = []
    seen_topics = set()
    for chunk in chunks:
        name = chunk["header"]
        if name not in seen_topics:
            h_pos = chunk.get("heading_pos", {"page": 1, "y0": 0})
            all_headings.append((h_pos["page"], h_pos["y0"], name))
            seen_topics.add(name)

    all_headings.sort() # Sort by Page, then Y

    # 2. Assign images to the physically preceding heading
    image_to_topic = {}
    for img in uploaded_images:
        img_page = img["page_number"]
        img_y = img["bbox"][1]
        current_owner = all_headings[0][2] if all_headings else "Introduction"
        
        for h_page, h_y, h_name in all_headings:
            if (h_page < img_page) or (h_page == img_page and h_y <= img_y):
                current_owner = h_name
            else:
                break
        
        if current_owner not in image_to_topic: image_to_topic[current_owner] = []
        image_to_topic[current_owner].append(img["gridfs_id"])
        print(f"  [MAP] Image on P{img_page} at Y{img_y:.0f} -> {current_owner}")

    for chunk in chunks:
        chunk["associated_image_ids"] = image_to_topic.get(chunk["header"], [])
    return chunks
