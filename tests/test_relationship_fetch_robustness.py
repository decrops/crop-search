from __future__ import annotations

import unittest

from crop_search_framework.dev_tools.fetch_web import infer_document_type
from crop_search_framework.relationship_pipeline import (
    _is_low_value_text,
    _looks_like_html,
    crop_reference_url,
)


class _Crop:
    def __init__(self, crop_id, label):
        self.crop_id = crop_id
        self.label = label


class DocumentTypeTests(unittest.TestCase):
    def test_pdf_endpoint_with_query_string_is_pdf(self):
        # Publishers serve PDFs from a /pdf endpoint with a version query.
        self.assertEqual(
            infer_document_type("https://www.mdpi.com/2071-1050/10/8/2834/pdf?version=1", "application/octet-stream"),
            "pdf",
        )
        self.assertEqual(
            infer_document_type("https://x.biomedcentral.com/counter/pdf/10.1/y", "application/pdf"),
            "pdf",
        )

    def test_html_and_plain_pdf_unchanged(self):
        self.assertEqual(infer_document_type("https://en.wikipedia.org/wiki/Sugar_beet", "text/html"), "html")
        self.assertEqual(infer_document_type("https://x.org/paper.pdf", "application/octet-stream"), "pdf")


class LowValueTextTests(unittest.TestCase):
    def test_challenge_pages_and_short_text_are_low_value(self):
        self.assertTrue(_is_low_value_text(""))
        self.assertTrue(_is_low_value_text("Checking your browser before accessing the site."))
        self.assertTrue(_is_low_value_text("Just a moment... Please enable JavaScript and cookies."))

    def test_real_article_text_is_usable(self):
        self.assertFalse(_is_low_value_text("Sugar beet is grown in rotation with cereals to break disease cycles. " * 60))

    def test_long_article_mentioning_captcha_is_not_rejected(self):
        # A real long article that merely mentions "captcha" must not be dropped.
        text = "This paper studies bot detection. " * 100 + " captcha"
        self.assertFalse(_is_low_value_text(text))

    def test_looks_like_html(self):
        self.assertTrue(_looks_like_html(b"  \n<!DOCTYPE html><html>"))
        self.assertTrue(_looks_like_html(b"<html lang='en'>"))
        self.assertFalse(_looks_like_html(b"%PDF-1.7\n..."))


class CropReferenceUrlTests(unittest.TestCase):
    def test_reference_url_from_label(self):
        self.assertEqual(crop_reference_url(_Crop("sugar_beet", "Sugar beet")),
                         "https://en.wikipedia.org/wiki/Sugar_beet")
        # Synonym labels rely on Wikipedia redirects (Oilseed rape -> Rapeseed).
        self.assertEqual(crop_reference_url(_Crop("rapeseed", "Oilseed rape")),
                         "https://en.wikipedia.org/wiki/Oilseed_rape")


if __name__ == "__main__":
    unittest.main()
