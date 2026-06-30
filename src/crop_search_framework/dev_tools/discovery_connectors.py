from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Tuple
from urllib.parse import quote_plus, urlparse

import requests  # noqa: F401  (kept for back-compat; calls now go through _get_json)

from ..quality import score_source_result_with_components
from .http_client import HttpClient

# Shared client. Discovery sets a cache dir via ``configure_client``; unit tests
# patch ``_get_json`` directly. Routing every connector through this one seam is
# what gives all providers retry/backoff/cache (WS-3).
_CLIENT = HttpClient()


def configure_client(client: HttpClient) -> None:
    global _CLIENT
    _CLIENT = client


def _get_json(url, params=None, headers=None, timeout: int = 20):
    return _CLIENT.get_json(url, params=params, headers=headers, timeout=timeout)


OPENALEX_WORKS_URL = "https://api.openalex.org/works"
CROSSREF_WORKS_URL = "https://api.crossref.org/v1/works"
GOOGLE_BOOKS_URL = "https://www.googleapis.com/books/v1/volumes"
OPEN_LIBRARY_SEARCH_URL = "https://openlibrary.org/search.json"
EUROPE_PMC_SEARCH_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
DOAJ_ARTICLE_SEARCH_URL = "https://doaj.org/api/search/articles/"
INTERNET_ARCHIVE_SEARCH_URL = "https://archive.org/advancedsearch.php"
DOAB_SEARCH_URL = "https://directory.doabooks.org/rest/search"
WIKIPEDIA_API_URL = "https://en.wikipedia.org/w/api.php"

# Relevance floor for broad/noisy connectors (Internet Archive, Wikipedia),
# mirroring the DuckDuckGo HTML fallback gate. Topical scholarly APIs that
# already match the query server-side are left ungated, like OpenAlex/Crossref.
MIN_RELEVANCE_SCORE = 4


def connector_results_for_tier(
    query: str,
    crop: str,
    source_tier_id: str,
    max_results: int,
    user_agent: str,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Route a query to free, key-less discovery APIs for the given source tier.

    Every tier now resolves to at least one structured API so the DuckDuckGo
    HTML fallback in search_web is only ever a last-resort top-up, never the
    primary discovery path for any tier.
    """
    if source_tier_id == "peer_reviewed_science":
        return gather_provider_results(
            (
                lambda: openalex_results(query, crop, max_results, user_agent),
                lambda: crossref_results(query, crop, max_results, user_agent),
                lambda: europe_pmc_results(query, crop, max_results, user_agent),
                lambda: doaj_results(query, crop, max_results, user_agent),
            )
        )
    if source_tier_id == "textbook_reference":
        return gather_provider_results(
            (
                lambda: google_books_results(query, crop, max_results, user_agent),
                lambda: open_library_results(query, crop, max_results, user_agent),
                lambda: internet_archive_results(query, crop, max_results, user_agent),
                lambda: doab_results(query, crop, max_results, user_agent),
            )
        )
    if source_tier_id == "international_institution":
        return gather_provider_results(
            (
                lambda: openalex_results(query, crop, max_results, user_agent),
                lambda: wikipedia_results(query, crop, max_results, user_agent),
            )
        )
    if source_tier_id == "extension_publication":
        return gather_provider_results(
            (
                lambda: openalex_results(query, crop, max_results, user_agent),
                lambda: internet_archive_results(query, crop, max_results, user_agent),
                lambda: wikipedia_results(query, crop, max_results, user_agent),
            )
        )
    if source_tier_id == "industry_grower_guide":
        return gather_provider_results(
            (
                lambda: openalex_results(query, crop, max_results, user_agent),
                lambda: wikipedia_results(query, crop, max_results, user_agent),
            )
        )
    if source_tier_id == "reference_encyclopedia":
        # Encyclopedia-only routing. This tier is opt-in (not in the default
        # tier_order); without this branch it would resolve to no provider and
        # silently return nothing.
        return gather_provider_results(
            (
                lambda: wikipedia_results(query, crop, max_results, user_agent),
            )
        )
    return [], []


def relevance_gate(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Drop low-signal results from noisy connectors using the shared scorer."""
    return [item for item in results if item.get("score", 0) >= MIN_RELEVANCE_SCORE]


def gather_provider_results(providers: Iterable[Any]) -> Tuple[List[Dict[str, Any]], List[str]]:
    results: List[Dict[str, Any]] = []
    errors: List[str] = []
    for provider in providers:
        try:
            results.extend(provider())
        except Exception as exc:
            errors.append(str(exc))
    return results, errors


def openalex_results(query: str, crop: str, max_results: int, user_agent: str) -> List[Dict[str, Any]]:
    response = _get_json(
        OPENALEX_WORKS_URL,
        params={"search": query, "per-page": max_results},
        headers={"User-Agent": user_agent},
        timeout=20,
    )
    payload = response.json()
    records = []
    for item in payload.get("results", []):
        title = item.get("display_name") or "OpenAlex work"
        open_access = item.get("open_access") or {}
        primary_location = item.get("primary_location") or {}
        source_url = (
            open_access.get("oa_url")
            or primary_location.get("pdf_url")
            or primary_location.get("landing_page_url")
            or item.get("doi")
            or item.get("id")
        )
        if not source_url:
            continue
        access_status = "open_full_text" if open_access.get("oa_url") or primary_location.get("pdf_url") else "metadata_only"
        snippet = science_snippet(
            provider="OpenAlex",
            publication_year=item.get("publication_year"),
            source_name=((primary_location.get("source") or {}).get("display_name") or ""),
            doi=item.get("doi", ""),
        )
        records.append(
            discovery_result(
                query=query,
                crop=crop,
                title=title,
                source_url=source_url,
                snippet=snippet,
                discovery_method="openalex",
                access_status=access_status,
                source_metadata={
                    "provider": "openalex",
                    "openalex_id": item.get("id", ""),
                    "doi": item.get("doi", ""),
                    "publication_year": item.get("publication_year"),
                    "is_open_access": bool(open_access.get("is_oa")),
                    "open_access_status": open_access.get("oa_status", ""),
                },
            )
        )
    return records


def crossref_results(query: str, crop: str, max_results: int, user_agent: str) -> List[Dict[str, Any]]:
    response = _get_json(
        CROSSREF_WORKS_URL,
        params={"query": query, "rows": max_results},
        headers={"User-Agent": user_agent},
        timeout=20,
    )
    payload = response.json()
    records = []
    for item in (payload.get("message") or {}).get("items", []):
        title = first_string(item.get("title")) or "Crossref work"
        doi = item.get("DOI", "")
        source_url = item.get("URL") or ("https://doi.org/{0}".format(doi) if doi else "")
        if not source_url:
            continue
        year = published_year(item)
        container = first_string(item.get("container-title"))
        snippet = science_snippet(
            provider="Crossref",
            publication_year=year,
            source_name=container,
            doi=doi,
        )
        # WS-4: capture link/license/ISSN/container so OA resolution + license
        # filtering have something to act on (previously only type/publisher).
        links = [
            {
                "url": link.get("URL", ""),
                "content_type": link.get("content-type", ""),
                "intended_application": link.get("intended-application", ""),
            }
            for link in (item.get("link") or [])
            if link.get("URL")
        ]
        licenses = [lic.get("URL", "") for lic in (item.get("license") or []) if lic.get("URL")]
        records.append(
            discovery_result(
                query=query,
                crop=crop,
                title=title,
                source_url=source_url,
                snippet=snippet,
                discovery_method="crossref",
                access_status="metadata_only",
                source_metadata={
                    "provider": "crossref",
                    "doi": doi,
                    "publication_year": year,
                    "type": item.get("type", ""),
                    "publisher": item.get("publisher", ""),
                    "container_title": container,
                    "issn": item.get("ISSN", []),
                    "links": links,
                    "licenses": licenses,
                },
            )
        )
    return records


def google_books_results(query: str, crop: str, max_results: int, user_agent: str) -> List[Dict[str, Any]]:
    response = _get_json(
        GOOGLE_BOOKS_URL,
        params={"q": query, "maxResults": max_results},
        headers={"User-Agent": user_agent},
        timeout=20,
    )
    payload = response.json()
    records = []
    for item in payload.get("items", []):
        volume = item.get("volumeInfo") or {}
        access = item.get("accessInfo") or {}
        title = volume.get("title") or "Google Books volume"
        is_open = access.get("publicDomain") or access.get("viewability") == "ALL_PAGES"
        source_url = (
            access.get("webReaderLink")
            if is_open and access.get("webReaderLink")
            else volume.get("previewLink") or volume.get("infoLink") or item.get("selfLink")
        )
        if not source_url:
            continue
        records.append(
            discovery_result(
                query=query,
                crop=crop,
                title=title,
                source_url=source_url,
                snippet=book_snippet("Google Books", volume),
                discovery_method="google_books",
                access_status="open_full_text" if is_open else "metadata_only",
                source_metadata={
                    "provider": "google_books",
                    "google_books_id": item.get("id", ""),
                    "publication_year": year_from_text(volume.get("publishedDate", "")),
                    "authors": volume.get("authors", []),
                    "publisher": volume.get("publisher", ""),
                    "viewability": access.get("viewability", ""),
                    "public_domain": bool(access.get("publicDomain")),
                },
            )
        )
    return records


def open_library_results(query: str, crop: str, max_results: int, user_agent: str) -> List[Dict[str, Any]]:
    response = _get_json(
        OPEN_LIBRARY_SEARCH_URL,
        params={"q": query, "fields": "key,title,author_name,first_publish_year,ia,availability", "limit": max_results},
        headers={"User-Agent": user_agent},
        timeout=20,
    )
    payload = response.json()
    records = []
    for item in payload.get("docs", []):
        title = item.get("title") or "Open Library work"
        archive_ids = item.get("ia") or []
        if archive_ids:
            source_url = "https://archive.org/details/{0}".format(quote_plus(archive_ids[0]))
            access_status = "open_full_text"
        else:
            source_url = "https://openlibrary.org{0}".format(item.get("key", ""))
            access_status = "metadata_only"
        records.append(
            discovery_result(
                query=query,
                crop=crop,
                title=title,
                source_url=source_url,
                snippet=book_snippet("Open Library", item),
                discovery_method="open_library",
                access_status=access_status,
                source_metadata={
                    "provider": "open_library",
                    "open_library_key": item.get("key", ""),
                    "publication_year": item.get("first_publish_year"),
                    "authors": item.get("author_name", []),
                    "archive_ids": archive_ids,
                },
            )
        )
    return records


def europe_pmc_results(query: str, crop: str, max_results: int, user_agent: str) -> List[Dict[str, Any]]:
    response = _get_json(
        EUROPE_PMC_SEARCH_URL,
        params={"query": query, "format": "json", "pageSize": max_results, "resultType": "core"},
        headers={"User-Agent": user_agent},
        timeout=20,
    )
    payload = response.json()
    records = []
    for item in ((payload.get("resultList") or {}).get("result") or []):
        title = item.get("title") or "Europe PMC article"
        doi = item.get("doi", "")
        full_text_urls = (item.get("fullTextUrlList") or {}).get("fullTextUrl") or []
        oa_url = next(
            (u.get("url") for u in full_text_urls if u.get("availability") in ("Open access", "Free")),
            "",
        )
        source_url = (
            oa_url
            or ("https://doi.org/{0}".format(doi) if doi else "")
            or (
                "https://europepmc.org/article/{0}/{1}".format(item.get("source", "MED"), item["id"])
                if item.get("id")
                else ""
            )
        )
        if not source_url:
            continue
        is_open = bool(oa_url) or item.get("isOpenAccess") == "Y"
        snippet = science_snippet(
            provider="Europe PMC",
            publication_year=item.get("pubYear"),
            source_name=item.get("journalTitle") or (item.get("journalInfo") or {}).get("journal", {}).get("title", ""),
            doi=doi,
        )
        records.append(
            discovery_result(
                query=query,
                crop=crop,
                title=title,
                source_url=source_url,
                snippet=snippet,
                discovery_method="europe_pmc",
                access_status="open_full_text" if is_open else "metadata_only",
                source_metadata={
                    "provider": "europe_pmc",
                    "doi": doi,
                    "publication_year": item.get("pubYear"),
                    "pmid": item.get("pmid", ""),
                    "pmcid": item.get("pmcid", ""),
                    "is_open_access": is_open,
                    "source_db": item.get("source", ""),
                },
            )
        )
    return records


def doaj_results(query: str, crop: str, max_results: int, user_agent: str) -> List[Dict[str, Any]]:
    response = _get_json(
        DOAJ_ARTICLE_SEARCH_URL + quote_plus(query),
        params={"pageSize": max_results},
        headers={"User-Agent": user_agent},
        timeout=20,
    )
    payload = response.json()
    records = []
    for item in payload.get("results", []):
        bibjson = item.get("bibjson") or {}
        title = bibjson.get("title") or "DOAJ article"
        doi = ""
        landing = ""
        for identifier in bibjson.get("identifier", []):
            if identifier.get("type") == "doi":
                doi = identifier.get("id", "")
        for link in bibjson.get("link", []):
            if link.get("type") == "fulltext":
                landing = link.get("url", "")
        source_url = landing or ("https://doi.org/{0}".format(doi) if doi else "")
        if not source_url:
            continue
        journal = (bibjson.get("journal") or {}).get("title", "")
        snippet = science_snippet(
            provider="DOAJ",
            publication_year=bibjson.get("year"),
            source_name=journal,
            doi=doi,
        )
        records.append(
            discovery_result(
                query=query,
                crop=crop,
                title=title,
                source_url=source_url,
                snippet=snippet,
                discovery_method="doaj",
                access_status="open_full_text",
                source_metadata={
                    "provider": "doaj",
                    "doi": doi,
                    "publication_year": bibjson.get("year"),
                    "journal": journal,
                    "is_open_access": True,
                },
            )
        )
    return records


def internet_archive_results(query: str, crop: str, max_results: int, user_agent: str) -> List[Dict[str, Any]]:
    archive_query = "({0}) AND mediatype:texts".format(query)
    response = _get_json(
        INTERNET_ARCHIVE_SEARCH_URL,
        params=[
            ("q", archive_query),
            ("fl[]", "identifier"),
            ("fl[]", "title"),
            ("fl[]", "year"),
            ("fl[]", "creator"),
            ("rows", max_results),
            ("output", "json"),
        ],
        headers={"User-Agent": user_agent},
        timeout=20,
    )
    payload = response.json()
    records = []
    for item in ((payload.get("response") or {}).get("docs") or []):
        identifier = item.get("identifier")
        if not identifier:
            continue
        title = item.get("title") or "Internet Archive text"
        if isinstance(title, list):
            title = title[0] if title else "Internet Archive text"
        source_url = "https://archive.org/details/{0}".format(identifier)
        records.append(
            discovery_result(
                query=query,
                crop=crop,
                title=title,
                source_url=source_url,
                snippet=book_snippet("Internet Archive", {
                    "authors": item.get("creator", []),
                    "first_publish_year": item.get("year"),
                }),
                discovery_method="internet_archive",
                access_status="open_full_text",
                source_metadata={
                    "provider": "internet_archive",
                    "archive_identifier": identifier,
                    "publication_year": year_from_text(str(item.get("year", ""))),
                    "authors": item.get("creator", []),
                },
            )
        )
    return records


def doab_results(query: str, crop: str, max_results: int, user_agent: str) -> List[Dict[str, Any]]:
    response = _get_json(
        DOAB_SEARCH_URL,
        params={"query": query, "expand": "metadata", "limit": max_results},
        headers={"User-Agent": user_agent},
        timeout=20,
    )
    payload = response.json()
    records = []
    for item in payload if isinstance(payload, list) else []:
        metadata = {entry.get("key"): entry.get("value") for entry in item.get("metadata", [])}
        title = metadata.get("dc.title") or item.get("name") or "DOAB book"
        handle = item.get("handle", "")
        source_url = metadata.get("dc.identifier.uri") or (
            "https://directory.doabooks.org/handle/{0}".format(handle) if handle else ""
        )
        if not source_url:
            continue
        author = metadata.get("dc.contributor.author", "")
        records.append(
            discovery_result(
                query=query,
                crop=crop,
                title=title,
                source_url=source_url,
                snippet=book_snippet("DOAB open-access book", {
                    "authors": [author] if author else [],
                    "first_publish_year": metadata.get("dc.date.issued", ""),
                    "description": metadata.get("dc.description.abstract", ""),
                }),
                discovery_method="doab",
                access_status="open_full_text",
                source_metadata={
                    "provider": "doab",
                    "doab_handle": handle,
                    "publication_year": year_from_text(str(metadata.get("dc.date.issued", ""))),
                    "publisher": metadata.get("publisher.name", ""),
                    "is_open_access": True,
                },
            )
        )
    return records


def wikipedia_results(query: str, crop: str, max_results: int, user_agent: str) -> List[Dict[str, Any]]:
    response = _get_json(
        WIKIPEDIA_API_URL,
        params={
            "action": "query",
            "list": "search",
            "srsearch": query,
            "srlimit": max_results,
            "format": "json",
        },
        headers={"User-Agent": user_agent},
        timeout=20,
    )
    payload = response.json()
    records = []
    for item in ((payload.get("query") or {}).get("search") or []):
        title = item.get("title", "Wikipedia article")
        source_url = "https://en.wikipedia.org/wiki/{0}".format(quote_plus(title.replace(" ", "_")))
        snippet_html = item.get("snippet", "")
        snippet = re.sub(r"<[^>]+>", "", snippet_html)
        records.append(
            discovery_result(
                query=query,
                crop=crop,
                title=title,
                source_url=source_url,
                snippet="Wikipedia reference. {0}".format(snippet),
                discovery_method="wikipedia",
                access_status="open_full_text",
                source_metadata={
                    "provider": "wikipedia",
                    "pageid": item.get("pageid"),
                },
            )
        )
    return records


def discovery_result(
    query: str,
    crop: str,
    title: str,
    source_url: str,
    snippet: str,
    discovery_method: str,
    access_status: str,
    source_metadata: Dict[str, Any],
) -> Dict[str, Any]:
    domain = urlparse(source_url).netloc.lower()
    score, components = score_source_result_with_components(query, title, snippet, domain, source_url, crop)
    return {
        "title": title,
        "source_url": source_url,
        "document_type": "pdf" if source_url.lower().endswith(".pdf") else "html",
        "search_snippet": snippet,
        "source_domain": domain,
        "score": score,
        "score_components": components,
        "discovery_method": discovery_method,
        "access_status": access_status,
        "source_metadata": source_metadata,
    }


def first_string(value: Any) -> str:
    if isinstance(value, list) and value:
        return str(value[0])
    if isinstance(value, str):
        return value
    return ""


def published_year(item: Dict[str, Any]) -> Any:
    for key in ("published-print", "published-online", "published", "created"):
        parts = ((item.get(key) or {}).get("date-parts") or [])
        if parts and parts[0]:
            return parts[0][0]
    return None


def year_from_text(value: str) -> Any:
    if len(value) >= 4 and value[:4].isdigit():
        return int(value[:4])
    return None


def science_snippet(provider: str, publication_year: Any, source_name: str, doi: str) -> str:
    parts = [provider, "scholarly metadata"]
    if publication_year:
        parts.append(str(publication_year))
    if source_name:
        parts.append(source_name)
    if doi:
        parts.append("DOI {0}".format(doi.replace("https://doi.org/", "")))
    return ". ".join(parts) + "."


def book_snippet(provider: str, item: Dict[str, Any]) -> str:
    authors = item.get("authors") or item.get("author_name") or []
    if isinstance(authors, list):
        author_text = ", ".join(str(author) for author in authors[:3])
    else:
        author_text = str(authors)
    year = item.get("publishedDate") or item.get("first_publish_year") or ""
    description = item.get("description", "")
    if isinstance(description, dict):
        description = description.get("value", "")
    parts = [provider, "book/reference metadata"]
    if year:
        parts.append(str(year))
    if author_text:
        parts.append(author_text)
    if description:
        parts.append(str(description)[:180])
    return ". ".join(parts) + "."
