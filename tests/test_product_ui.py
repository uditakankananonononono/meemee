from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_product_ui_has_all_views_and_explicit_endpoint_contract():
    html = (ROOT / "webapp/index.html").read_text()
    js = (ROOT / "webapp/assets/app.js").read_text()
    css = (ROOT / "webapp/assets/app.css").read_text()
    for label in (
        "Timeline / Inbox",
        "Monitor Center",
        "Source Health",
        "Context Controls",
        "WHY THIS IS HERE",
    ):
        assert label in html
    for endpoint in (
        "/v1/product/timeline",
        "/v1/product/monitors",
        "/v1/product/source-health",
        "/v1/product/context-controls",
        "/v1/product/provenance",
    ):
        assert endpoint in js
    assert "PRODUCT_ENDPOINTS" in js and "textContent" in js
    assert ".workspace-card" in css and "#provenance-content" in css


def test_product_ui_avoids_inline_handlers_and_unsafe_html():
    html = (ROOT / "webapp/index.html").read_text()
    js = (ROOT / "webapp/assets/app.js").read_text()
    assert "onclick=" not in html
    assert "innerHTML" not in js
