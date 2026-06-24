from __future__ import annotations

import unittest
from unittest.mock import patch

from crop_search_framework.dev_tools import discovery_connectors as dc


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self.payload


class EuropePmcTests(unittest.TestCase):
    @patch("crop_search_framework.dev_tools.discovery_connectors._get_json")
    def test_prefers_open_full_text_url(self, mock_get) -> None:
        mock_get.return_value = FakeResponse({
            "resultList": {"result": [
                {
                    "id": "111", "source": "MED", "title": "Wheat optimum temperature response",
                    "doi": "10.1/abc", "pubYear": "2024", "isOpenAccess": "Y",
                    "journalTitle": "Field Crops Research",
                    "fullTextUrlList": {"fullTextUrl": [
                        {"availability": "Open access", "url": "https://ex.org/wheat.pdf"},
                    ]},
                },
                {
                    "id": "222", "source": "MED", "title": "Wheat cold tolerance",
                    "doi": "10.1/def", "pubYear": "2020", "isOpenAccess": "N",
                },
            ]}
        })
        results = dc.europe_pmc_results("wheat optimum temperature", "wheat", 2, "ua")
        self.assertEqual(results[0]["access_status"], "open_full_text")
        self.assertEqual(results[0]["source_url"], "https://ex.org/wheat.pdf")
        self.assertEqual(results[0]["discovery_method"], "europe_pmc")
        self.assertEqual(results[1]["access_status"], "metadata_only")
        self.assertEqual(results[1]["source_url"], "https://doi.org/10.1/def")


class DoajTests(unittest.TestCase):
    @patch("crop_search_framework.dev_tools.discovery_connectors._get_json")
    def test_extracts_fulltext_link_and_marks_open(self, mock_get) -> None:
        mock_get.return_value = FakeResponse({"results": [
            {"bibjson": {
                "title": "Wheat water requirement under irrigation", "year": "2022",
                "journal": {"title": "Agronomy"},
                "identifier": [{"type": "doi", "id": "10.5/xyz"}],
                "link": [{"type": "fulltext", "url": "https://doaj.example/article"}],
            }},
        ]})
        results = dc.doaj_results("wheat water requirement", "wheat", 1, "ua")
        self.assertEqual(results[0]["access_status"], "open_full_text")
        self.assertEqual(results[0]["source_url"], "https://doaj.example/article")
        self.assertEqual(results[0]["discovery_method"], "doaj")


class InternetArchiveTests(unittest.TestCase):
    @patch("crop_search_framework.dev_tools.discovery_connectors._get_json")
    def test_connector_returns_raw_rows_without_dropping(self, mock_get) -> None:
        # Relevance filtering moved OUT of connectors (WS-1): the connector must
        # return every row so the discovery ledger is complete. The low-signal
        # CIA row survives here and is gated downstream by relevance_gate().
        mock_get.return_value = FakeResponse({"response": {"docs": [
            {"identifier": "wheatprod", "title": "Wheat production and crop physiology", "year": "1995"},
            {"identifier": "cia-readingroom-xyz", "title": "CIA Reading Room document", "year": "1951"},
        ]}})
        results = dc.internet_archive_results("wheat crop production temperature", "wheat", 5, "ua")
        ids = {r["source_metadata"]["archive_identifier"] for r in results}
        self.assertIn("wheatprod", ids)
        self.assertIn("cia-readingroom-xyz", ids)
        # The gate is now a standalone, downstream-applied function.
        kept_ids = {r["source_metadata"]["archive_identifier"] for r in dc.relevance_gate(results)}
        self.assertIn("wheatprod", kept_ids)
        self.assertNotIn("cia-readingroom-xyz", kept_ids)


class CrossrefTests(unittest.TestCase):
    @patch("crop_search_framework.dev_tools.discovery_connectors._get_json")
    def test_captures_link_license_issn_for_oa_filtering(self, mock_get) -> None:
        mock_get.return_value = FakeResponse({"message": {"items": [
            {
                "title": ["Wheat nitrogen response"], "DOI": "10.1/abc", "URL": "https://doi.org/10.1/abc",
                "type": "journal-article", "publisher": "Elsevier",
                "container-title": ["Field Crops Research"], "ISSN": ["0378-4290"],
                "license": [{"URL": "https://creativecommons.org/licenses/by/4.0/"}],
                "link": [{"URL": "https://x.org/wheat.pdf", "content-type": "application/pdf",
                          "intended-application": "text-mining"}],
            },
        ]}})
        results = dc.crossref_results("wheat nitrogen", "wheat", 1, "ua")
        meta = results[0]["source_metadata"]
        self.assertEqual(meta["type"], "journal-article")
        self.assertEqual(meta["container_title"], "Field Crops Research")
        self.assertEqual(meta["issn"], ["0378-4290"])
        self.assertEqual(meta["licenses"], ["https://creativecommons.org/licenses/by/4.0/"])
        self.assertEqual(meta["links"][0]["content_type"], "application/pdf")


class DoabTests(unittest.TestCase):
    @patch("crop_search_framework.dev_tools.discovery_connectors._get_json")
    def test_parses_dublin_core_metadata_list(self, mock_get) -> None:
        mock_get.return_value = FakeResponse([
            {"handle": "20.500/67595", "name": "Agronomy", "metadata": [
                {"key": "dc.title", "value": "Agronomy"},
                {"key": "dc.date.issued", "value": "2020"},
                {"key": "dc.identifier.uri", "value": "https://directory.doabooks.org/handle/20.500/67595"},
            ]},
        ])
        results = dc.doab_results("agronomy", "wheat", 1, "ua")
        self.assertEqual(results[0]["source_url"], "https://directory.doabooks.org/handle/20.500/67595")
        self.assertEqual(results[0]["discovery_method"], "doab")
        self.assertEqual(results[0]["access_status"], "open_full_text")


class WikipediaTests(unittest.TestCase):
    @patch("crop_search_framework.dev_tools.discovery_connectors._get_json")
    def test_strips_html_and_builds_article_url(self, mock_get) -> None:
        mock_get.return_value = FakeResponse({"query": {"search": [
            {"title": "Wheat", "pageid": 1, "snippet": "Wheat is a <span>cereal</span> grain grown worldwide"},
        ]}})
        results = dc.wikipedia_results("wheat cultivation temperature", "wheat", 1, "ua")
        self.assertEqual(results[0]["source_url"], "https://en.wikipedia.org/wiki/Wheat")
        self.assertNotIn("<span>", results[0]["search_snippet"])
        self.assertEqual(results[0]["discovery_method"], "wikipedia")


class TierRoutingTests(unittest.TestCase):
    def test_every_tier_resolves_to_at_least_one_connector(self) -> None:
        tiers = [
            "peer_reviewed_science",
            "textbook_reference",
            "international_institution",
            "extension_publication",
            "industry_grower_guide",
        ]
        for tier in tiers:
            with patch.object(dc, "openalex_results", return_value=[{"x": 1}]), \
                 patch.object(dc, "crossref_results", return_value=[]), \
                 patch.object(dc, "europe_pmc_results", return_value=[]), \
                 patch.object(dc, "doaj_results", return_value=[]), \
                 patch.object(dc, "google_books_results", return_value=[{"x": 1}]), \
                 patch.object(dc, "open_library_results", return_value=[]), \
                 patch.object(dc, "internet_archive_results", return_value=[]), \
                 patch.object(dc, "doab_results", return_value=[]), \
                 patch.object(dc, "wikipedia_results", return_value=[{"x": 1}]):
                results, errors = dc.connector_results_for_tier("wheat temp", "wheat", tier, 3, "ua")
                self.assertTrue(results, f"tier {tier} returned no connector results")
                self.assertEqual(errors, [])

    def test_unknown_tier_returns_empty(self) -> None:
        results, errors = dc.connector_results_for_tier("q", "wheat", "made_up_tier", 3, "ua")
        self.assertEqual(results, [])
        self.assertEqual(errors, [])

    def test_provider_error_is_captured_not_raised(self) -> None:
        def boom():
            raise RuntimeError("api down")
        results, errors = dc.gather_provider_results((boom, lambda: [{"x": 1}]))
        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 1)
        self.assertIn("api down", errors[0])


if __name__ == "__main__":
    unittest.main()
