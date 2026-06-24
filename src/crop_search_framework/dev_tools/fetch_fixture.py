from __future__ import annotations

from .common import emit_response, load_fixtures, load_request


def main() -> None:
    request = load_request()
    source_url = request["source_url"]
    fixtures = load_fixtures()
    for source in fixtures["sources"]:
        if source["source_url"] == source_url:
            emit_response(
                {
                    "source_id": source["source_id"],
                    "source_url": source["source_url"],
                    "title": source["title"],
                    "document_type": source["document_type"],
                    "body": source["body"],
                    "snippet": source["snippet"],
                    "fetch_status": "fetched",
                }
            )
            return
    emit_response(
        {
            "source_url": source_url,
            "document_type": "other",
            "body": "",
            "snippet": "",
            "fetch_status": "not_found",
        }
    )


if __name__ == "__main__":
    main()
