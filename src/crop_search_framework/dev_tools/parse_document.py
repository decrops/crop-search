from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Tuple

from bs4 import BeautifulSoup

from .common import emit_response, load_request, repo_root
from ..quality import claim_has_layout_artifact, claim_has_source_header_artifact, claim_quality_score


DATE_PATTERNS = (
    r"\b(?:19|20)\d{2}\b",
    r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\s+\d{1,2},?\s+(?:19|20)\d{2}\b",
)

BANNED_PHRASES = (
    "table of contents",
    "acknowledgements",
    "author acknowledgements",
    "bibliography",
    "cooperative extension service",
    "equal opportunity provider",
    "for more information",
    "more information about",
    "literature cited",
    "program discrimination complaint",
    "worldwide web",
)

REFERENCE_HEADING_PATTERN = re.compile(r"^(references|bibliography|literature cited|works cited)$", flags=re.IGNORECASE)
TOC_HEADING_PATTERN = re.compile(r"^(contents|table of contents|index)$", flags=re.IGNORECASE)
CHAPTER_HEADING_PATTERN = re.compile(r"^(chapter|section)\s+\d+\b", flags=re.IGNORECASE)


def sentence_split(text: str) -> List[str]:
    chunks = re.split(r"(?<=[.!?])\s+|\s{2,}", text.strip())
    sentences: List[str] = []
    for chunk in chunks:
        for piece in re.split(r"\s*;\s*", chunk):
            cleaned = chunk_cleanup(piece)
            if cleaned:
                sentences.append(cleaned)
    return sentences


def chunk_cleanup(text: str) -> str:
    return cleanup_whitespace(text.replace("\x0c", " "))


def cleanup_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def extract_html_blocks(root: BeautifulSoup) -> List[str]:
    candidate_selectors = (
        "article p",
        "article li",
        "main p",
        "main li",
        ".field-item p",
        ".entry-content p",
        "body p",
    )
    for selector in candidate_selectors:
        nodes = root.select(selector)
        blocks = [cleanup_whitespace(node.get_text(" ", strip=True)) for node in nodes]
        blocks = [block for block in blocks if len(block) >= 40]
        if blocks:
            return blocks
    return []


def parse_html(artifact_path: Path) -> Dict[str, Any]:
    soup = BeautifulSoup(artifact_path.read_bytes(), "lxml")
    for node in soup(["script", "style", "noscript", "nav", "footer", "aside", "header"]):
        node.decompose()
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    blocks = extract_html_blocks(soup)
    if not blocks:
        body = soup.select_one("body")
        if body:
            blocks = [cleanup_whitespace(body.get_text(" ", strip=True))]
    text = "\n\n".join(blocks)
    publication_date_hint = ""
    for attr in (
        ("meta", {"property": "article:published_time"}),
        ("meta", {"name": "date"}),
        ("meta", {"name": "dc.date"}),
    ):
        node = soup.find(attr[0], attrs=attr[1])
        if node and node.get("content"):
            publication_date_hint = cleanup_whitespace(node["content"])
            break
    if not publication_date_hint:
        for pattern in DATE_PATTERNS:
            match = re.search(pattern, text[:2000], flags=re.IGNORECASE)
            if match:
                publication_date_hint = match.group(0)
                break
    return {
        "raw_text": text,
        "snippet": text[:280],
        "title_hint": title,
        "publication_date_hint": publication_date_hint,
    }


def pdf_noise_line(line: str) -> bool:
    lowered = line.lower()
    stripped = line.strip()
    if not stripped:
        return True
    if any(phrase in lowered for phrase in BANNED_PHRASES):
        return True
    if REFERENCE_HEADING_PATTERN.match(stripped) or TOC_HEADING_PATTERN.match(stripped):
        return True
    if likely_index_entry(stripped):
        return True
    if likely_reference_entry(stripped):
        return True
    if table_like_text(stripped):
        return True
    if "contents" in lowered and len(stripped.split()) < 8:
        return True
    if stripped.startswith("page ") or stripped.startswith("table of"):
        return True
    if re.search(r"_[_\s]{4,}", stripped):
        return True
    if len(re.findall(r"\d", stripped)) > len(re.findall(r"[a-zA-Z]", stripped)) and len(stripped) > 20:
        return True
    if lowered.startswith("ia nrcs") or lowered.startswith("usda is an equal opportunity"):
        return True
    return False


def pdf_paragraphs(raw_text: str) -> List[str]:
    paragraphs: List[str] = []
    current: List[str] = []
    skipping_back_matter = False
    for line in raw_text.splitlines():
        cleaned = cleanup_whitespace(line)
        if not cleaned:
            if current:
                paragraphs.append(cleanup_whitespace(" ".join(current)))
                current = []
            continue
        if REFERENCE_HEADING_PATTERN.match(cleaned):
            skipping_back_matter = True
            if current:
                paragraphs.append(cleanup_whitespace(" ".join(current)))
                current = []
            continue
        if TOC_HEADING_PATTERN.match(cleaned):
            if current:
                paragraphs.append(cleanup_whitespace(" ".join(current)))
                current = []
            continue
        if skipping_back_matter and not CHAPTER_HEADING_PATTERN.match(cleaned):
            continue
        if CHAPTER_HEADING_PATTERN.match(cleaned):
            skipping_back_matter = False
        if pdf_noise_line(cleaned):
            if current:
                paragraphs.append(cleanup_whitespace(" ".join(current)))
                current = []
            continue
        if cleaned.endswith("-"):
            current.append(cleaned[:-1])
        else:
            current.append(cleaned)
    if current:
        paragraphs.append(cleanup_whitespace(" ".join(current)))
    return [paragraph for paragraph in paragraphs if len(paragraph) >= 40]


def parse_pdf(artifact_path: Path) -> Dict[str, Any]:
    completed = subprocess.run(
        ["pdftotext", "-raw", "-nopgbrk", str(artifact_path), "-"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(stderr or "pdftotext failed")
    raw_text = completed.stdout.decode("utf-8", errors="replace")
    paragraphs = pdf_paragraphs(raw_text)
    text = "\n\n".join(paragraphs)
    publication_date_hint = ""
    for pattern in DATE_PATTERNS:
        match = re.search(pattern, text[:2500], flags=re.IGNORECASE)
        if match:
            publication_date_hint = match.group(0)
            break
    return {
        "raw_text": text,
        "snippet": text[:280],
        "title_hint": "",
        "publication_date_hint": publication_date_hint,
    }


def select_claims(sentences: List[str], source_title: str, query: str, crop: str) -> List[str]:
    scored_claims: List[Tuple[int, str]] = []
    seen = set()
    for sentence in sentences:
        lower = sentence.lower()
        word_count = len(sentence.split())
        if word_count < 6 or word_count > 45:
            continue
        if any(phrase in lower for phrase in BANNED_PHRASES):
            continue
        if likely_reference_entry(sentence) or likely_index_entry(sentence) or table_like_text(sentence):
            continue
        if claim_has_layout_artifact(sentence):
            continue
        if claim_has_source_header_artifact(sentence, source_title):
            continue
        if lower.count("table ") > 0 or lower.count("figure ") > 0:
            continue
        if "_" in sentence:
            continue
        if len(re.findall(r"\d", sentence)) > max(12, len(re.findall(r"[A-Za-z]", sentence))):
            continue
        score = claim_quality_score(sentence, source_title, query, crop)
        if score < 4:
            continue
        normalized = lower.strip(". ")
        if normalized in seen:
            continue
        seen.add(normalized)
        scored_claims.append((score, sentence))
    scored_claims.sort(key=lambda item: item[0], reverse=True)
    return [sentence for _, sentence in scored_claims[:8]]


def likely_reference_entry(text: str) -> bool:
    lowered = text.lower()
    if re.search(r"\bdoi:\s*10\.\d{4,9}/", lowered):
        return True
    if re.search(r"\b(?:19|20)\d{2}\.\s+[A-Z]", text) and re.search(r"\b[A-Z][a-z]+,\s+[A-Z]\.", text):
        return True
    if lowered.startswith(("http://", "https://")):
        return True
    return False


def likely_index_entry(text: str) -> bool:
    if len(text.split()) > 18:
        return False
    return bool(re.search(r"\b[A-Za-z][A-Za-z\s-]+,\s*\d+(?:,\s*\d+){1,}\b", text))


def table_like_text(text: str) -> bool:
    tokens = text.split()
    if len(tokens) < 6:
        return False
    numeric_tokens = sum(1 for token in tokens if re.search(r"\d", token))
    if numeric_tokens >= 5 and numeric_tokens >= len(tokens) / 2:
        return True
    if len(re.findall(r"\b[A-Z]{2,}\b", text)) >= 4 and numeric_tokens >= 2:
        return True
    return False


def evidence_fragment_labels(fragments: List[str]) -> List[Dict[str, str]]:
    return [{"label": evidence_label(fragment), "text": fragment} for fragment in fragments]


def evidence_label(fragment: str) -> str:
    lowered = fragment.lower()
    if likely_reference_entry(fragment):
        return "reference"
    if "table " in lowered:
        return "table"
    if "figure " in lowered:
        return "figure"
    if "abstract" in lowered[:30]:
        return "abstract"
    if CHAPTER_HEADING_PATTERN.match(fragment):
        return "chapter"
    return "claim"


def main() -> None:
    request = load_request()
    document = request["document"]
    query = request.get("query", "")
    crop = request.get("crop", "")
    artifact_path = repo_root() / document["artifact_path"]
    document_type = document.get("document_type", "other")

    if document_type == "pdf":
        parsed = parse_pdf(artifact_path)
    else:
        parsed = parse_html(artifact_path)

    sentences = sentence_split(parsed["raw_text"])
    candidate_claims = select_claims(
        sentences=sentences,
        source_title=parsed["title_hint"] or document.get("title_hint", ""),
        query=query,
        crop=crop,
    )
    evidence_fragments = candidate_claims[:5]
    emit_response(
        {
            "parser_used": "parse-document",
            "snippet": parsed["snippet"],
            "raw_text": parsed["raw_text"],
            "candidate_claims": candidate_claims,
            "evidence_fragments": evidence_fragments,
            "evidence_fragment_labels": evidence_fragment_labels(evidence_fragments),
            "publication_date_hint": parsed["publication_date_hint"],
            "title_hint": parsed["title_hint"],
            "failures": [] if parsed["raw_text"] else ["missing_text"],
            "status": "parsed" if parsed["raw_text"] else "failed",
        }
    )


if __name__ == "__main__":
    main()
