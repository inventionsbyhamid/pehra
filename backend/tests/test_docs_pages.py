"""The site's documentation lives at /docs and /docs/<page>: the server serves the prerendered pages there (laid out as
web/scripts/prerender.mjs writes them), and the API's own pages moved to /api/docs, so no API route shadows them."""
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from test_api import StubManager
from watchful.agent.llm import FakeLLM
from watchful.api.app import WEB_DIST, create_app
from watchful.brand import BRAND
from watchful.config import Settings

PAGES = {"/docs": f"Documentation | {BRAND.name}", "/docs/getting-started": f"Getting started | {BRAND.name} docs",
         "/docs/self-hosting": f"Self hosting | {BRAND.name} docs"}


def page(path: str, title: str) -> str:
    return f'<html><head><title>{title}</title></head><body><div id="root" data-prerendered="{path}"></div></body></html>'


def built(tmp_path: Path) -> Path:
    """A dist as prerender.mjs leaves it: <page>.html and <page>/index.html, nested for /docs/<page>."""
    dist = tmp_path / "dist"
    (dist / "docs").mkdir(parents=True)
    (dist / "index.html").write_text(page("/", "the landing page"))
    (dist / "app.html").write_text("<html>the app shell</html>")
    for path, title in PAGES.items():
        name = path[1:]
        (dist / f"{name}.html").write_text(page(path, title))
        (dist / name).mkdir(exist_ok=True)
        (dist / name / "index.html").write_text(page(path, title))
    return dist


def client(dist: Path, tmp_path: Path, **settings) -> TestClient:
    s = Settings(_env_file=None, session_secret="s" * 32, data_dir=tmp_path, web_dist=dist, **settings)
    return TestClient(create_app(s, llm=FakeLLM([]), manager=StubManager()))


@pytest.mark.parametrize("api_docs", [False, True])
def test_docs_pages_are_the_sites_prerendered_pages(tmp_path, api_docs):
    c = client(built(tmp_path), tmp_path, api_docs=api_docs)
    for path, title in PAGES.items():
        for url in (path, path + "/"):
            r = c.get(url)
            assert r.status_code == 200, url
            assert f"<title>{title}</title>" in r.text and f'data-prerendered="{path}"' in r.text, url
    assert "the app shell" in c.get("/docs/no-such-page").text  # an unknown page is the app's, not an error
    assert ("swagger" in c.get("/api/docs").text.lower()) == api_docs


def test_the_built_docs_pages_have_their_own_titles(tmp_path):
    if not (WEB_DIST / "docs" / "index.html").is_file():
        pytest.skip("the web app is not built (cd web && npm run build)")
    c = client(WEB_DIST, tmp_path)
    titles = set()
    for path in ("/docs", "/docs/getting-started", "/docs/how-it-works", "/docs/privacy-and-safety",
                 "/docs/self-hosting", "/docs/aws"):
        r = c.get(path)
        assert r.status_code == 200 and f'data-prerendered="{path}"' in r.text, path
        titles.add(re.search(r"<title>([^<]*)</title>", r.text).group(1))
    assert len(titles) == 6 and all(BRAND.name in t for t in titles)
