"""Tests for the site permission store in riemann/core/permissions.py."""

import json

import pytest
from PySide6.QtCore import QUrl

from riemann.core.permissions import (
    ALLOW,
    ASK,
    BLOCK,
    KINDS,
    SitePermissions,
    decide,
    site_of,
)


def test_defaults_are_safe(tmp_path):
    store = SitePermissions(str(tmp_path / "p.json"))
    for key in ("camera", "microphone", "screen", "location", "notifications", "clipboard"):
        assert store.get("https://a.example", key) == ASK
    assert store.get("https://a.example", "popups") == BLOCK
    assert store.get("https://a.example", "insecure_content") == BLOCK
    assert store.get("https://a.example", "autoplay") == ALLOW
    assert store.get("https://a.example", "javascript") == ALLOW


def test_site_of_normalises_urls():
    assert site_of("https://Docs.Example.com/a/b?q=1") == "https://docs.example.com"
    assert site_of(QUrl("http://x.example:80/")) == "http://x.example"
    assert site_of("http://x.example:8080/") == "http://x.example:8080"
    assert site_of("file:///home/me/a.html") == ""
    assert site_of("about:blank") == ""


def test_site_decisions_override_defaults_and_persist(tmp_path):
    path = str(tmp_path / "p.json")
    store = SitePermissions(path)
    store.set("https://meet.example/room", "camera", ALLOW)
    store.set_default("notifications", BLOCK)
    assert store.get("https://meet.example/other", "camera") == ALLOW
    assert store.get("https://else.example", "camera") == ASK

    again = SitePermissions(path)
    assert again.get("https://meet.example", "camera") == ALLOW
    assert again.get("https://else.example", "notifications") == BLOCK
    assert again.sites() == {"https://meet.example": {"camera": ALLOW}}

    again.set("https://meet.example", "camera", None)
    assert SitePermissions(path).sites() == {}


def test_incognito_store_reads_but_never_writes(tmp_path):
    path = str(tmp_path / "p.json")
    SitePermissions(path).set("https://a.example", "camera", BLOCK)
    private = SitePermissions(path, persist=False)
    assert private.get("https://a.example", "camera") == BLOCK
    private.set("https://b.example", "location", ALLOW)
    assert private.get("https://b.example", "location") == ALLOW
    assert "https://b.example" not in json.load(open(path))["sites"]


def test_bad_values_are_rejected_and_bad_files_ignored(tmp_path):
    path = tmp_path / "p.json"
    path.write_text('{"defaults": {"camera": "maybe", "popups": "ask"}, "sites": {"x": {"camera": 3}}}')
    store = SitePermissions(str(path))
    assert store.get("https://a.example", "camera") == ASK
    assert store.get("https://a.example", "popups") == BLOCK
    with pytest.raises(ValueError):
        store.set("https://a.example", "popups", ASK)
    path.write_text("not json")
    assert SitePermissions(str(path)).sites() == {}


def test_decide_combines_settings(tmp_path):
    store = SitePermissions(str(tmp_path / "p.json"))
    store.set("https://a.example", "camera", ALLOW)
    assert decide(store, "https://a.example", ["camera"]) == ALLOW
    assert decide(store, "https://a.example", ["camera", "microphone"]) == ASK
    store.set("https://a.example", "microphone", BLOCK)
    assert decide(store, "https://a.example", ["camera", "microphone"]) == BLOCK
    assert decide(store, "https://a.example", []) == BLOCK


def test_every_kind_has_a_valid_default():
    assert len({k.key for k in KINDS}) == len(KINDS)
    for k in KINDS:
        assert k.default in k.choices
