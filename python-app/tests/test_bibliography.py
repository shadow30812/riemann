"""Tests for riemann/core/bibliography.py."""

import json

from riemann.core.bibliography import citation_key, split_authors, to_bibtex, to_csl_json

ATTENTION = {
    "title": "Attention Is All You Need",
    "authors": "Ashish Vaswani, Noam Shazeer, Niki Parmar",
    "year": "2017",
    "doi": "",
    "arxiv_id": "1706.03762",
    "file_path": "/papers/attention.pdf",
}


def test_authors_in_either_style():
    assert split_authors("Ashish Vaswani, Noam Shazeer") == [
        {"family": "Vaswani", "given": "Ashish"},
        {"family": "Shazeer", "given": "Noam"},
    ]
    assert split_authors("Gödel, Kurt and Turing, Alan") == [
        {"family": "Gödel", "given": "Kurt"},
        {"family": "Turing", "given": "Alan"},
    ]
    assert split_authors("") == []


def test_keys_are_conventional_and_unique():
    assert citation_key(ATTENTION) == "vaswani2017attention"
    assert citation_key({"title": "The Art of Proof", "authors": "Kurt Gödel", "year": "1931"}) == "godel1931art"
    text = to_bibtex([ATTENTION, dict(ATTENTION, title="Attention Again")])
    assert "@misc{vaswani2017attention," in text and "@misc{vaswani2017attentiona," in text


def test_bibtex_fields_and_escaping():
    text = to_bibtex([dict(ATTENTION, title="Q&A on 100% {nets}", doi="10.1/x_y")])
    assert "@article{" in text
    assert "title = {{Q\\&A on 100\\% \\{nets\\}}}" in text
    assert "author = {Vaswani, Ashish and Shazeer, Noam and Parmar, Niki}" in text
    assert "doi = {10.1/x\\_y}" in text
    assert "eprint = {1706.03762}" in text


def test_csl_json_for_zotero():
    items = json.loads(to_csl_json([ATTENTION]))
    assert items[0]["id"] == "vaswani2017attention"
    assert items[0]["author"][0] == {"family": "Vaswani", "given": "Ashish"}
    assert items[0]["issued"] == {"date-parts": [[2017]]}
    assert items[0]["URL"] == "https://arxiv.org/abs/1706.03762"
