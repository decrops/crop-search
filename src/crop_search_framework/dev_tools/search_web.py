from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup

from .common import emit_response, load_request, user_agent
from .discovery_connectors import connector_results_for_tier
from ..quality import score_source_result


SEARCH_URL = "https://html.duckduckgo.com/html/"


def tokenize(value: str) -> Set[str]:
    return set(re.findall(r"[a-z0-9]+", value.lower()))


def direct_url(raw_href: Optional[str]) -> Optional[str]:
    if not raw_href:
        return None
    normalized = raw_href
    if normalized.startswith("//"):
        normalized = "https:" + normalized
    parsed = urlparse(normalized)
    if "duckduckgo.com" not in parsed.netloc:
        return normalized
    query = parse_qs(parsed.query)
    uddg = query.get("uddg")
    return uddg[0] if uddg else normalized


def duckduckgo_results(query: str, crop: str, max_results: int) -> List[Dict[str, Any]]:
    response = requests.get(
        SEARCH_URL,
        params={"q": query},
        headers={"User-Agent": user_agent()},
        timeout=25,
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "lxml")
    results: List[Dict[str, Any]] = []
    seen_urls = set()
    for result in soup.select(".result"):
        anchor = result.select_one(".result__a")
        snippet_node = result.select_one(".result__snippet")
        target_url = direct_url(anchor.get("href") if anchor else None)
        if not target_url:
            continue
        if target_url in seen_urls:
            continue
        seen_urls.add(target_url)
        parsed = urlparse(target_url)
        domain = parsed.netloc.lower()
        title = anchor.get_text(" ", strip=True) if anchor else target_url
        snippet = snippet_node.get_text(" ", strip=True) if snippet_node else ""
        score = score_source_result(query, title, snippet, domain, target_url, crop)
        if score < 4:
            continue
        results.append(
            {
                "title": title,
                "source_url": target_url,
                "document_type": "pdf" if target_url.lower().endswith(".pdf") else "html",
                "search_snippet": snippet,
                "source_domain": domain,
                "score": score,
                "discovery_method": "duckduckgo_html",
                "access_status": "unknown",
                "source_metadata": {},
            }
        )
    return results[:max_results]


def dedupe_results(results: List[Dict[str, Any]], max_results: int) -> List[Dict[str, Any]]:
    deduped: List[Dict[str, Any]] = []
    seen_urls = set()
    for result in sorted(results, key=lambda item: item["score"], reverse=True):
        source_url = result["source_url"]
        if source_url in seen_urls:
            continue
        seen_urls.add(source_url)
        deduped.append(result)
        if len(deduped) >= max_results:
            break
    return deduped


def main() -> None:
    request = load_request()
    query = request.get("query", "")
    crop = request.get("crop", "")
    source_tier_id = request.get("source_tier_id", "")
    max_results = int(request.get("max_results", 3))

    connector_results, provider_errors = connector_results_for_tier(
        query=query,
        crop=crop,
        source_tier_id=source_tier_id,
        max_results=max_results,
        user_agent=user_agent(),
    )

    results = list(connector_results)
    if len(results) < max_results:
        try:
            results.extend(duckduckgo_results(query, crop, max_results))
        except Exception as exc:
            provider_errors.append("duckduckgo_html: {0}".format(exc))

    emit_response({"results": dedupe_results(results, max_results), "provider_errors": provider_errors})


if __name__ == "__main__":
    main()
