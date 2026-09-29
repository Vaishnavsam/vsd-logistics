from __future__ import annotations

import argparse
from pathlib import Path


def build_pages(web_directory: Path, output_directory: Path) -> None:
    output_directory.mkdir(parents=True, exist_ok=True)
    product_page = (web_directory / "product.html").read_text(encoding="utf-8")
    for source, target in (
        ('href="/product"', 'href="#top"'),
        ('href="/"', 'href="#scope"'),
        ('href="/driver"', 'href="#scope"'),
        ('href="/product.css"', 'href="product.css"'),
        ('src="/product.js"', 'src="product.js"'),
        ("Open operations", "Explore product scope"),
        ("Explore the operations desk", "Explore the product"),
        ("Explore operations", "View the scope"),
        ("Try the real operations flow", "See the workflow"),
        ("Open the operations prototype", "Read the project scope"),
        ("Go to operations", "View the product"),
    ):
        product_page = product_page.replace(source, target)

    (output_directory / "index.html").write_text(product_page, encoding="utf-8")
    for asset in ("product.css", "product.js"):
        (output_directory / asset).write_bytes((web_directory / asset).read_bytes())


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the static GitHub Pages product preview.")
    parser.add_argument("--web-directory", type=Path, default=Path("web"))
    parser.add_argument("--output-directory", type=Path, default=Path("_site"))
    arguments = parser.parse_args()
    build_pages(arguments.web_directory, arguments.output_directory)


if __name__ == "__main__":
    main()
