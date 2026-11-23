"""Tests for citation lookup (riemann/core/citations.py) and the hover card."""

import os
import time

import pytest

from riemann.core import citations

CORPUS = os.path.join(os.path.dirname(__file__), "..", "..", "benchmarks", "corpus", "attention.pdf")


class FakeResponse:
    def __init__(self, status, data):
        self.status_code = status
        self._data = data

    def json(self):
        return self._data


class FakeSession:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url, params))
        for key, response in self.responses:
            if key in url or (params and key in str(params)):
                return response
        return FakeResponse(404, {})


LSTM = {
    "id": "https://openalex.org/W1",
    "title": "Long Short-Term Memory",
    "publication_year": 1997,
    "cited_by_count": 100000,
    "authorships": [
        {"author": {"display_name": "Sepp Hochreiter"}},
        {"author": {"display_name": "Jürgen Schmidhuber"}},
    ],
    "ids": {"doi": "https://doi.org/10.1162/neco.1997.9.8.1735"},
    "locations": [],
    "open_access": {"oa_url": ""},
}
TUTORIAL = dict(LSTM, title="Understanding LSTM: a tutorial", publication_year=1997, cited_by_count=10)


def test_title_and_year_are_guessed():
    title, year = citations.guess_title_and_year(
        "[13] Sepp Hochreiter and Jürgen Schmidhuber. Long short-term memory. Neural computation, 9(8):1735–1780, 1997."
    )
    assert (title, year) == ("Long short-term memory", 1997)


def test_lookup_keeps_only_close_title_matches():
    ref = "[13] Sepp Hochreiter and Jürgen Schmidhuber. Long short-term memory. Neural computation, 1997."
    session = FakeSession([("title.search", FakeResponse(200, {"results": [TUTORIAL, LSTM]}))])
    info = citations.lookup(ref, session=session)
    assert info["title"] == "Long Short-Term Memory"
    assert info["doi"] == "10.1162/neco.1997.9.8.1735"
    assert "publication_year:1996-1998" in str(session.calls[0][1])
    assert citations.best_link(info) == "https://doi.org/10.1162/neco.1997.9.8.1735"

    nothing = FakeSession(
        [("title.search", FakeResponse(200, {"results": [TUTORIAL]})), ("search", FakeResponse(200, {"results": []}))]
    )
    assert citations.lookup(ref, session=nothing) is None


def test_arxiv_ids_are_looked_up_directly():
    work = dict(LSTM, title="Generating Sequences", locations=[{"landing_page_url": "https://arxiv.org/abs/1308.0850"}])
    session = FakeSession([("arxiv:1308.0850", FakeResponse(200, work))])
    info = citations.lookup("[9] Alex Graves. Generating sequences. arXiv:1308.0850, 2013.", session=session)
    assert info["arxiv"] == "1308.0850"
    assert citations.best_link(info) == "https://arxiv.org/abs/1308.0850"


@pytest.mark.skipif(not os.path.exists(CORPUS), reason="benchmark corpus not downloaded")
def test_reference_text_is_read_from_the_bibliography():
    import riemann_core

    doc = riemann_core.PdfEngine().load_document(os.path.abspath(CORPUS), None)
    text = citations.reference_text_at(doc, 10, 453.45)
    assert text.startswith("[13] Sepp Hochreiter and Jürgen Schmidhuber. Long short-term memory.")
    assert "[14]" not in text
    assert citations.looks_like_reference(text)


@pytest.mark.skipif(not os.path.exists(CORPUS), reason="benchmark corpus not downloaded")
def test_hovering_a_citation_shows_its_reference(qapp):
    from riemann.ui.reader.tab import ReaderTab

    def pump(seconds, until=lambda: False):
        end = time.time() + seconds
        while time.time() < end and not until():
            qapp.processEvents()
            time.sleep(0.01)

    tab = ReaderTab()
    tab.resize(1100, 900)
    tab.show()
    tab.load_document(os.path.abspath(CORPUS))
    pump(15, lambda: tab.current_doc is not None)
    tab.jump_to_page(1)
    pump(3, lambda: bool(getattr(tab.page_widgets.get(1), "link_rects", None)))
    widget = tab.page_widgets[1]
    rect, url = next((r, u) for r, u in widget.link_rects if u.startswith("#page=11&top=453"))
    tab._hover_internal_link(url, widget, rect.center())
    pump(2, lambda: tab.citation_card.isVisible())
    assert tab.citation_card.isVisible()
    assert "Long short-term memory" in tab.citation_card.text.text()
    opened = []
    tab.window().open_url_beside = opened.append
    tab.citation_card.info = {
        "title": "Long Short-Term Memory",
        "arxiv": "",
        "doi": "10.1162/x",
        "authors": [],
        "cited_by": 1,
    }
    tab.citation_card._show_info()
    tab.citation_card._open()
    assert opened == ["https://doi.org/10.1162/x"]
    tab.cleanup()
