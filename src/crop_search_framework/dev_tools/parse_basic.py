from __future__ import annotations

import re
from typing import Any, Dict, List

from .common import emit_response, load_request


CLAIM_KEYWORDS = (
    "temperature",
    "temperatures",
    "warm",
    "moisture",
    "water",
    "soil",
    "emergence",
    "growth",
    "drainage",
)


def sentence_split(text: str) -> List[str]:
    chunks = re.split(r"(?<=[.!?])\s+", text.strip())
    return [chunk.strip() for chunk in chunks if chunk.strip()]


def select_claims(sentences: List[str]) -> List[str]:
    claims = []
    for sentence in sentences:
        lower = sentence.lower()
        if any(keyword in lower for keyword in CLAIM_KEYWORDS):
            claims.append(sentence)
    return claims


def main() -> None:
    request = load_request()
    document: Dict[str, Any] = request["document"]
    raw_text = document.get("body", "").strip()
    sentences = sentence_split(raw_text)
    candidate_claims = select_claims(sentences)
    emit_response(
        {
            "parser_used": "parse-basic",
            "snippet": document.get("snippet") or raw_text[:280],
            "raw_text": raw_text,
            "candidate_claims": candidate_claims,
            "failures": [] if raw_text else ["missing_body"],
            "status": "parsed" if raw_text else "failed",
        }
    )


if __name__ == "__main__":
    main()
