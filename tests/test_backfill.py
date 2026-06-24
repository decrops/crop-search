from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from crop_search_framework import backfill, corpus


class JunkDoiTests(unittest.TestCase):
    def test_flags_non_article_dois(self):
        for doi in ("10.7717/peerj.19802/table-4", "10.7717/peerj.12245/supp-4",
                    "10.7287/peerj.14965/reviews/1", "10.3410/f.1023703.281660",
                    "10.3974/geodb.2023.09.08.v1"):
            self.assertTrue(backfill.is_junk_doi(doi), doi)

    def test_passes_real_article_dois(self):
        for doi in ("10.1038/s41598-026-45892-5", "10.1007/s12571-013-0263-y"):
            self.assertFalse(backfill.is_junk_doi(doi), doi)


class RetryTests(unittest.TestCase):
    def test_backs_off_then_succeeds(self):
        seq = [mock.Mock(status_code=429), mock.Mock(status_code=200)]
        sleeps = []
        with mock.patch("requests.get", side_effect=seq):
            resp = backfill.http_get_with_retry("http://x", sleeper=sleeps.append)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(sleeps), 1)  # one backoff before the success

    def test_gives_up_after_max_retries(self):
        with mock.patch("requests.get", return_value=mock.Mock(status_code=503)):
            resp = backfill.http_get_with_retry("http://x", max_retries=3, sleeper=lambda s: None)
        self.assertIsNone(resp)


class ResolveOaTests(unittest.TestCase):
    def test_prefers_unpaywall_pdf(self):
        up = backfill._CachedResponse({"best_oa_location": {"url_for_pdf": "https://oa/x.pdf"}})
        with mock.patch.object(backfill, "http_get_with_retry", return_value=up):
            out = backfill.resolve_oa("10.1/x", "e@x.org", "ua")
        self.assertEqual(out, {"url": "https://oa/x.pdf", "resolver": "unpaywall"})

    def test_returns_none_when_no_oa(self):
        empty = backfill._CachedResponse({"best_oa_location": None})
        with mock.patch.object(backfill, "http_get_with_retry", return_value=empty):
            self.assertIsNone(backfill.resolve_oa("10.1/x", "e@x.org", "ua"))


def _fake_repo(tmp: Path) -> None:
    (tmp / "config/parameters").mkdir(parents=True)
    (tmp / "config/parameters/core-crop-parameters.json").write_text(json.dumps({
        "manifest_version": "0.3.0",
        "parameters": [{"parameter_id": "temperature.base_temperature", "label": "Base temperature",
                        "family": "temperature", "implementation_status": "active"}],
    }))
    raw = tmp / "exploration/raw/run-b"
    raw.mkdir(parents=True)
    (raw / "summary.json").write_text(json.dumps({"crop": "wheat"}))

    def cap(i, doi, access, text):
        (raw / "run-b-capture-{0:03d}.json".format(i)).write_text(json.dumps({
            "id": "run-b-capture-{0:03d}".format(i), "parameter_id": "temperature.base_temperature",
            "parameter_family": "temperature", "source_tier_id": "peer_reviewed_science",
            "source_url": "https://doi.org/{0}".format(doi), "final_url": "https://doi.org/{0}".format(doi),
            "source_title": "Doc {0}".format(i), "source_domain": "doi.org", "discovery_method": "crossref",
            "access_status": access, "document_type": "html", "query": "wheat base temp",
            "raw_text": text, "source_metadata": {"doi": doi},
        }))
    cap(1, "10.1/realarticle", "metadata_only", "")        # resolvable OA
    cap(2, "10.7717/peerj.1/table-4", "metadata_only", "")  # junk DOI -> excluded
    cap(3, "10.1/fulltext", "open_full_text", "Wheat base temperature is 0 C. " * 30)  # already full


class BackfillCorpusTests(unittest.TestCase):
    def test_excludes_junk_and_fetches_oa(self):
        with tempfile.TemporaryDirectory() as t:
            repo = Path(t)
            _fake_repo(repo)
            corpus.build_corpus(repo, "run-b")

            fake_tools = mock.Mock()
            fake_tools.invoke.side_effect = lambda name, payload: (
                {"artifact_path": "", "document_type": "html"} if name == "fetch-web"
                else {"raw_text": "Recovered full text about wheat base temperature 0 C. " * 20}
            )
            with mock.patch.object(backfill, "CommandToolRunner", return_value=fake_tools), \
                 mock.patch.object(backfill, "resolve_oa",
                                   side_effect=lambda doi, *a, **k: {"url": "https://oa/x", "resolver": "unpaywall"} if "real" in doi else None):
                summary = backfill.backfill_corpus(repo, "run-b", email="e@x.org")

            self.assertEqual(summary["excluded_junk"], 1)      # the /table-4 DOI
            self.assertEqual(summary["fetched_full_text"], 1)  # the real article
            # QA now counts the junk doc out of the Opus input set
            report = corpus.corpus_qa(repo, "run-b")
            self.assertEqual(report["excluded_from_opus_count"], 1)
            self.assertEqual(report["backfilled_full_text_count"], 1)
            self.assertEqual(report["metadata_only_count"], 0)  # the only metadata-only was backfilled


if __name__ == "__main__":
    unittest.main()
