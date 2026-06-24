from __future__ import annotations

import re
from typing import Dict, Iterable, List, Set
from urllib.parse import urlparse

from .source_tiers import source_tier_score_bonus


QUERY_TOPIC_KEYWORDS = {
    "temperature": {
        "temperature",
        "temperatures",
        "warm",
        "heat",
        "gdu",
        "germination",
        "emergence",
    },
    "moisture": {
        "moisture",
        "water",
        "evapotranspiration",
        "water table",
        "drainage",
        "drought",
        "rainfall",
    },
    "planting": {
        "planting",
        "date",
        "dates",
        "window",
        "emergence",
        "maturity",
        "silking",
    },
    "conditions": {
        "growing",
        "growth",
        "conditions",
        "soil",
        "season",
        "yield",
        "stress",
    },
}

PREFERRED_DOMAIN_FRAGMENTS = (
    "extension",
    "agronomy",
    "iastate",
    "purdue",
    "wisc",
    "corn",
    "usda",
    ".edu",
)

PREFERRED_SOURCE_TERMS = (
    "iowa state",
    "kansas state",
    "k-state",
    "purdue",
    "extension",
    "agronomy",
    "usda",
    "integrated crop management",
    "crop sciences",
    "research and demonstration farm",
)

LOW_SIGNAL_DOMAIN_FRAGMENTS = (
    "studylib",
    "yumpu",
    "solara",
    "pinterest",
    "facebook",
    "linkedin",
    "twitter",
)

OFF_TOPIC_TERMS = (
    "phosphorus",
    "potassium",
    "nutrient management",
    "manure",
    "biosolids",
    "compost",
    "pasture",
    "grazing",
    "hay",
    "limestone",
    "fertilizing pasture",
    "grid sampling",
    "cover crop",
    "drought monitor",
)

LANDING_TITLE_PATTERNS = (
    "integrated crop management",
    "forecast and assessment",
    "crop section",
    "explore this section",
)

CLAIM_PRIMARY_KEYWORDS = {
    "temperature",
    "temperatures",
    "warm",
    "heat",
    "gdu",
    "germinate",
    "germination",
    "emergence",
    "planting",
    "silking",
    "maturity",
    "moisture",
    "water",
    "evapotranspiration",
    "water table",
    "drainage",
    "drought",
    "yield",
    "growth",
    "stress",
}

CLAIM_OFF_TOPIC_TERMS = {
    "phosphorus",
    "potassium",
    "p2o5",
    "k2o",
    "manure",
    "biosolids",
    "compost",
    "pasture",
    "grazing",
    "hay",
    "limestone",
    "buffer ph",
    "soil sampling",
    "grid sampling",
    "cover crop",
    "drought monitor",
}

CLAIM_LAYOUT_ARTIFACT_TERMS = {
    "all rights reserved",
    "author acknowledgements",
    "date of planting 101-day",
    "equal opportunity provider",
    "extension manhattan",
    "h20 yield",
    "lsd0.05",
    "least significant difference",
    "leaf collars",
    "more information about",
    "seminal roots",
    "nodal roots",
    "program discrimination complaint",
    "radicle",
    "coleoptile",
    "worldwide web",
}

HEADER_BYLINE_TERMS = {
    "agronomy",
    "author",
    "breeder",
    "editor",
    "extension agronomist",
    "professor",
    "research & extension",
    "research and extension",
}

LOW_VALUE_NAVIGATION_TERMS = {
    "can be found on",
    "click here",
    "for more information",
    "more information about",
    "on pages",
    "see pages",
    "worldwide web",
}

CLAIM_PREDICATE_PATTERN = re.compile(
    r"\b("
    r"adapted|are|begins?|can|could|emerge[sd]?|exceed[s]?|germinates?|grow[s]?|"
    r"has|have|hold[s]?|is|may|might|must|need[s]?|occur[s]?|planted|produces?|"
    r"ranges?|recommended|require[sd]?|respond[s]?|sensitive|should|starts?|"
    r"tolerate[s]?|used|was|were|will"
    r")\b",
    flags=re.IGNORECASE,
)


def tokenize(value: str) -> Set[str]:
    return set(re.findall(r"[a-z0-9]+", value.lower()))


def infer_query_topics(query: str) -> Set[str]:
    lowered = query.lower()
    topics = set()
    for topic, keywords in QUERY_TOPIC_KEYWORDS.items():
        if any(keyword in lowered for keyword in keywords):
            topics.add(topic)
    return topics or {"conditions"}


def topic_keywords(topics: Iterable[str]) -> Set[str]:
    keywords = set()
    for topic in topics:
        keywords.update(QUERY_TOPIC_KEYWORDS.get(topic, set()))
    return keywords


def has_topic_match(text: str, topics: Iterable[str]) -> bool:
    lowered = text.lower()
    return any(keyword in lowered for keyword in topic_keywords(topics))


def score_source_result(
    query: str,
    title: str,
    snippet: str,
    domain: str,
    url: str,
    crop: str,
) -> int:
    """Stable int API. Delegates to the component variant (WS-1)."""
    score, _components = score_source_result_with_components(query, title, snippet, domain, url, crop)
    return score


def score_source_result_with_components(
    query: str,
    title: str,
    snippet: str,
    domain: str,
    url: str,
    crop: str,
):
    """Return ``(score, components)`` so the discovery ledger can record *why*
    a result scored as it did. ``score_source_result`` keeps its int contract by
    delegating here, so existing call sites are unchanged."""
    components: Dict[str, int] = {}

    def add(name: str, delta: int) -> None:
        if delta:
            components[name] = components.get(name, 0) + delta

    query_tokens = tokenize(query)
    title_tokens = tokenize(title)
    snippet_tokens = tokenize(snippet)
    url_tokens = tokenize(urlparse(url).path.replace("/", " "))
    haystack_tokens = title_tokens | snippet_tokens | url_tokens | tokenize(domain)
    score = len(query_tokens & haystack_tokens)
    add("token_overlap", score)

    lowered_text = " ".join([title.lower(), snippet.lower(), domain.lower(), url.lower()])
    topics = infer_query_topics(query)
    matched_topics = 0
    for topic in topics:
        topic_set = QUERY_TOPIC_KEYWORDS.get(topic, set())
        if any(keyword in lowered_text for keyword in topic_set):
            score += 4
            add("topic_match", 4)
            matched_topics += 1
        else:
            score -= 2
            add("topic_miss", -2)

    if crop.lower() in lowered_text:
        score += 3
        add("crop_mention", 3)
    elif "corn belt" in lowered_text:
        score += 2
        add("corn_belt_mention", 2)
    if any(fragment in domain for fragment in PREFERRED_DOMAIN_FRAGMENTS):
        score += 4
        add("preferred_domain", 4)
    if any(term in lowered_text for term in PREFERRED_SOURCE_TERMS):
        score += 3
        add("preferred_source_term", 3)
    tier_bonus = source_tier_score_bonus(title, snippet, domain, url)
    score += tier_bonus
    add("source_tier_bonus", tier_bonus)
    if crop.lower() not in lowered_text and not any(fragment in domain for fragment in PREFERRED_DOMAIN_FRAGMENTS):
        score -= 4
        add("crop_absent_penalty", -4)
    if domain.endswith(".gov"):
        score += 1
        add("gov_domain", 1)
    if any(fragment in domain for fragment in LOW_SIGNAL_DOMAIN_FRAGMENTS):
        score -= 10
        add("low_signal_domain", -10)
    if any(term in lowered_text for term in OFF_TOPIC_TERMS):
        score -= 8
        add("off_topic_term", -8)
    if is_landing_page_title(title) and shallow_url(url):
        score -= 6
        add("landing_page", -6)
    if matched_topics == 0:
        score -= 5
        add("no_topic_match", -5)
    return score, components


def is_landing_page_title(title: str) -> bool:
    lowered = title.lower()
    return any(pattern in lowered for pattern in LANDING_TITLE_PATTERNS) or lowered.endswith("| integrated crop management")


def shallow_url(url: str) -> bool:
    parts = [part for part in urlparse(url).path.split("/") if part]
    return len(parts) <= 2


def claim_has_primary_signal(text: str, source_title: str, query: str, crop: str) -> bool:
    lowered = " ".join([text.lower(), source_title.lower(), query.lower()])
    if crop.lower() in lowered and any(keyword in lowered for keyword in CLAIM_PRIMARY_KEYWORDS):
        return True
    return any(keyword in lowered for keyword in topic_keywords(infer_query_topics(query)))


def claim_is_off_topic(text: str, source_title: str) -> bool:
    lowered = " ".join([text.lower(), source_title.lower()])
    return any(term in lowered for term in CLAIM_OFF_TOPIC_TERMS)


def claim_has_source_header_artifact(text: str, source_title: str) -> bool:
    lowered = text.lower()
    word_count = len(text.split())
    if any(term in lowered for term in LOW_VALUE_NAVIGATION_TERMS):
        return True
    if "http://" in lowered or "https://" in lowered or "www." in lowered:
        return True
    if re.search(r"^\(?\d+\)?\s*[–-]\s+[A-Z]", text) and word_count <= 12:
        return True
    if re.search(r"\bpages?\s+\d+\b", lowered):
        return True
    if source_title and text_resembles_source_title(text, source_title):
        return True
    if "," in text and any(term in lowered for term in HEADER_BYLINE_TERMS) and word_count <= 14:
        return True
    if word_count <= 12 and not CLAIM_PREDICATE_PATTERN.search(text):
        if re.search(r"\d", text) or any(term in lowered for term in HEADER_BYLINE_TERMS):
            return True
    return False


def text_resembles_source_title(text: str, source_title: str) -> bool:
    text_tokens = meaningful_title_tokens(text)
    title_tokens = meaningful_title_tokens(source_title)
    if len(title_tokens) < 3 or len(text_tokens) < 3:
        return False
    overlap = len(text_tokens & title_tokens)
    return overlap / len(title_tokens) >= 0.6 and len(text.split()) <= 16


def meaningful_title_tokens(value: str) -> Set[str]:
    stopwords = {
        "and",
        "at",
        "for",
        "of",
        "the",
        "to",
        "university",
    }
    return {token for token in tokenize(value) if len(token) > 2 and token not in stopwords}


def claim_has_layout_artifact(text: str) -> bool:
    lowered = text.lower()
    if any(term in lowered for term in CLAIM_LAYOUT_ARTIFACT_TERMS):
        return True
    if "•" in text and len(re.findall(r"[A-Z]?[0-9]+", text)) >= 4:
        return True
    if re.search(r"\b\d+(?:-day|st|nd|rd|th)\b.*\b\d+(?:-day|st|nd|rd|th)\b", lowered):
        return True
    if len(re.findall(r"\b[A-Z]{2,}\b", text)) >= 5 and len(text.split()) < 35:
        return True
    if re.search(r"^\(?\d+\)?\s*[–-]\s+[A-Z]", text) and len(text.split()) <= 12:
        return True
    if len(text.split()) <= 10 and not CLAIM_PREDICATE_PATTERN.search(text) and re.search(r"\d", text):
        return True
    return False


def claim_quality_score(text: str, source_title: str, query: str, crop: str) -> int:
    lowered = text.lower()
    score = 0
    if claim_has_primary_signal(text, source_title, query, crop):
        score += 5
    if any(keyword in lowered for keyword in CLAIM_PRIMARY_KEYWORDS):
        score += 3
    if re.search(r"\d", text):
        score += 2
    if crop.lower() in lowered or crop.lower() in source_title.lower():
        score += 1
    if claim_is_off_topic(text, source_title):
        score -= 8
    if claim_has_layout_artifact(text):
        score -= 10
    if claim_has_source_header_artifact(text, source_title):
        score -= 10
    if "table " in lowered or "figure " in lowered:
        score -= 5
    if "_" in text or "contents" in lowered:
        score -= 6
    return score


def capture_relevance_score(capture: Dict[str, str], crop: str) -> int:
    return score_source_result(
        capture.get("query", ""),
        capture.get("source_title", "") or capture.get("search_title", ""),
        capture.get("search_snippet", "") or capture.get("snippet", ""),
        capture.get("source_domain", ""),
        capture.get("final_url", "") or capture.get("source_url", ""),
        crop,
    )


def capture_has_crop_signal(capture: Dict[str, str], crop: str) -> bool:
    combined = " ".join(
        [
            capture.get("source_title", ""),
            capture.get("search_title", ""),
            capture.get("search_snippet", ""),
            capture.get("snippet", ""),
            capture.get("final_url", ""),
            capture.get("source_url", ""),
        ]
    ).lower()
    return crop.lower() in combined or "corn belt" in combined


def capture_has_preferred_domain(capture: Dict[str, str]) -> bool:
    domain = capture.get("source_domain", "").lower()
    return any(fragment in domain for fragment in PREFERRED_DOMAIN_FRAGMENTS)


def capture_has_preferred_source_term(capture: Dict[str, str]) -> bool:
    combined = " ".join(
        [
            capture.get("source_title", ""),
            capture.get("search_title", ""),
            capture.get("search_snippet", ""),
            capture.get("snippet", ""),
        ]
    ).lower()
    return any(term in combined for term in PREFERRED_SOURCE_TERMS)
