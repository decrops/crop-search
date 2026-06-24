from __future__ import annotations

import re
from typing import Any, Dict, List, Set

from .common import emit_response, load_fixtures, load_request


def tokenize(value: str) -> Set[str]:
    return set(re.findall(r"[a-z0-9]+", value.lower()))


def score_result(query_tokens: Set[str], source: Dict[str, Any]) -> int:
    source_tokens = set(source.get("tags", [])) | tokenize(source.get("title", "")) | tokenize(
        source.get("snippet", "")
    )
    return len(query_tokens & source_tokens)


def main() -> None:
    request = load_request()
    fixtures = load_fixtures()
    query_tokens = tokenize(request.get("query", ""))
    max_results = int(request.get("max_results", 3))

    ranked: List[Dict[str, Any]] = []
    for source in fixtures["sources"]:
        score = score_result(query_tokens, source)
        if score == 0:
            continue
        ranked.append(
            {
                "source_id": source["source_id"],
                "title": source["title"],
                "source_url": source["source_url"],
                "document_type": source["document_type"],
                "snippet": source["snippet"],
                "score": score,
            }
        )

    ranked.sort(key=lambda item: item["score"], reverse=True)
    emit_response({"results": ranked[:max_results]})


if __name__ == "__main__":
    main()
