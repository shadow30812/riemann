import io
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

import pytest

from riemann.ui import sidecar


class FakeDoc:
    def __init__(self, pages):
        self.pages = pages
        self.page_count = len(pages)

    def get_page_text(self, i):
        return self.pages[i]


def test_link_citations():
    text = "See [p. 3] and [pp. 4, 7]; not [note]."
    linked = sidecar.link_citations(text)
    assert "[p. [3](page:3)]" in linked
    assert "[4](page:4), [7](page:7)" in linked
    assert "[note]" in linked


def test_document_context_keeps_pages_near_current():
    doc = FakeDoc(["a" * 100 for _ in range(10)])
    context = sidecar.document_context(doc, current_page=5, budget=350)
    assert "[Page 6]" in context
    assert "[Page 1]" not in context
    assert context.startswith("(Only pages near page 6")

    whole = sidecar.document_context(FakeDoc(["x", "y"]), 0)
    assert whole == "[Page 1]\nx\n\n[Page 2]\ny"


def test_sse_events_stops_at_done():
    stream = io.BytesIO(
        b'event: x\ndata: {"a": 1}\n\ndata: not json\ndata: {"b": 2}\ndata: [DONE]\ndata: {"c": 3}\n'
    )
    assert list(sidecar._sse_events(stream)) == [{"a": 1}, {"b": 2}]


@pytest.fixture
def fake_ollama():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            body = json.dumps({"models": [{"name": "llama3:8b"}, {"name": "qwen3:14b"}]}).encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            Handler.last_request = request
            self.send_response(200)
            self.end_headers()
            for piece in ("Hello", " world", " [p. 2]"):
                self.wfile.write(json.dumps({"message": {"content": piece}}).encode() + b"\n")
            self.wfile.write(json.dumps({"done": True}).encode() + b"\n")

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    with patch.object(sidecar, "ollama_url", return_value=url):
        yield Handler
    server.shutdown()


def test_ollama_models_and_streaming(fake_ollama):
    assert sidecar.list_models("ollama", "") == ["llama3:8b", "qwen3:14b"]
    pieces = list(
        sidecar.stream_chat("ollama", "", "llama3:8b", "system text", [{"role": "user", "content": "hi"}])
    )
    assert "".join(pieces) == "Hello world [p. 2]"
    sent = fake_ollama.last_request
    assert sent["model"] == "llama3:8b"
    assert sent["messages"][0] == {"role": "system", "content": "system text"}
    assert sent["messages"][1]["content"] == "hi"


def test_http_errors_are_reported(fake_ollama):
    with (
        patch.object(sidecar, "ollama_url", return_value="http://127.0.0.1:9"),
        pytest.raises(sidecar.ProviderError),
    ):
        sidecar.list_models("ollama", "")


def test_panel_asks_for_a_document_first(qtbot):
    with patch.object(sidecar, "api_key", return_value=""):
        panel = sidecar.AiSidecarPanel()
    panel.ask("What is this about?")
    assert panel.messages[-1]["role"] == "notice"
    assert "Open a PDF" in panel.messages[-1]["content"]


def test_keys_file_is_owner_only_and_migrates_settings(tmp_path, monkeypatch):
    path = tmp_path / "cfg" / "Riemann" / "ai.env"
    monkeypatch.setenv("RIEMANN_AI_ENV", str(path))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    settings = sidecar._settings()
    settings.setValue("ai/keys/anthropic", "sk-ant-old")
    try:
        sidecar._migrate_settings_keys()
        assert not settings.contains("ai/keys/anthropic")
        assert sidecar.api_key("anthropic") == "sk-ant-old"
        if os.name != "nt":
            assert path.stat().st_mode & 0o777 == 0o600
            assert path.parent.stat().st_mode & 0o777 == 0o700

            path.chmod(0o644)
            sidecar.read_keys_file()
            assert path.stat().st_mode & 0o777 == 0o600

        sidecar.write_keys_file({"OPENAI_API_KEY": "sk-new", "GEMINI_API_KEY": ""})
        assert sidecar.read_keys_file() == {"OPENAI_API_KEY": "sk-new"}
        monkeypatch.setenv("OPENAI_API_KEY", "from-env")
        assert sidecar.api_key("openai") == "from-env"
        assert sidecar.api_key("anthropic") == ""
    finally:
        settings.remove("ai/keys")


def test_keys_file_parsing(tmp_path, monkeypatch):
    path = tmp_path / "ai.env"
    path.write_text('# c\nexport ANTHROPIC_API_KEY="quoted"\nbad line\nOPENAI_API_KEY = plain \n')
    monkeypatch.setenv("RIEMANN_AI_ENV", str(path))
    assert sidecar.read_keys_file() == {"ANTHROPIC_API_KEY": "quoted", "OPENAI_API_KEY": "plain"}
