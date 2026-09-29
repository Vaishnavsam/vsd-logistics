from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.build_pages import build_pages


class PagesBuildTests(unittest.TestCase):
    def test_builds_product_site_with_working_relative_assets_and_no_backend_links(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary_directory:
            output_directory = Path(temporary_directory) / "site"
            build_pages(project_root / "web", output_directory)

            page = (output_directory / "index.html").read_text(encoding="utf-8")
            self.assertIn('href="product.css"', page)
            self.assertIn('src="product.js"', page)
            self.assertIn('href="#top"', page)
            self.assertIn('href="#scope"', page)
            self.assertNotIn('href="/', page)
            self.assertTrue((output_directory / "product.css").is_file())
            self.assertTrue((output_directory / "product.js").is_file())
            self.assertFalse((output_directory / "app.js").exists())


if __name__ == "__main__":
    unittest.main()
