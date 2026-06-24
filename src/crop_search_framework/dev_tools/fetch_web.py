from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any, Dict
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from .common import emit_response, load_request, repo_root, user_agent


def safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "-" for ch in value)


def infer_document_type(final_url: str, content_type: str) -> str:
    lowered = content_type.lower()
    if "pdf" in lowered or final_url.lower().endswith(".pdf"):
        return "pdf"
    if "html" in lowered:
        return "html"
    if "json" in lowered:
        return "json"
    if "csv" in lowered:
        return "csv"
    return "other"


def pick_suffix(final_url: str, document_type: str) -> str:
    if document_type == "pdf":
        return ".pdf"
    parsed = urlparse(final_url)
    suffix = Path(parsed.path).suffix
    if suffix:
        return suffix
    if document_type == "json":
        return ".json"
    if document_type == "csv":
        return ".csv"
    return ".html"


def extract_title_hint(text: str) -> str:
    soup = BeautifulSoup(text, "lxml")
    if soup.title and soup.title.get_text(strip=True):
        return soup.title.get_text(" ", strip=True)
    return ""


def main() -> None:
    request = load_request()
    source_url = request["source_url"]
    run_id = request["run_id"]
    source_index = int(request.get("source_index", 0))

    response = requests.get(
        source_url,
        headers={"User-Agent": user_agent()},
        timeout=35,
        allow_redirects=True,
    )
    response.raise_for_status()

    final_url = response.url
    content_type = response.headers.get("content-type", "application/octet-stream")
    document_type = infer_document_type(final_url, content_type)

    fetch_dir = repo_root() / "exploration" / "raw" / run_id / "fetched"
    fetch_dir.mkdir(parents=True, exist_ok=True)
    domain = urlparse(final_url).netloc.lower()
    suffix = pick_suffix(final_url, document_type)
    filename = "{0:03d}-{1}{2}".format(source_index, safe_name(domain or "source"), suffix)
    artifact_path = fetch_dir / filename
    artifact_path.write_bytes(response.content)

    title_hint = ""
    if document_type == "html":
        title_hint = extract_title_hint(response.text)

    emit_response(
        {
            "source_url": source_url,
            "final_url": final_url,
            "artifact_path": str(artifact_path.relative_to(repo_root())),
            "document_type": document_type,
            "content_type": content_type,
            "title_hint": title_hint,
            "content_length": len(response.content),
            "fetch_status": "fetched",
            "access_status": "open_full_text",
            "status_code": response.status_code,
        }
    )


if __name__ == "__main__":
    main()
