"""
Tests for the AI search engine (riemann/core/ai_server.py). They never
download the model: a keyword embedder stands in for it, and the test of the
real ONNX embedder runs only when RIEMANN_TEST_AI_MODELS names a folder that
already holds the model files.
"""

import io
import os

import numpy as np
import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from riemann.core import ai_server as srv

KEYWORDS = ("attention", "garden", "topology", "alpha", "beta")


class KeywordEmbedder:
    def __init__(self):
        self.calls = 0

    def encode(self, texts):
        self.calls += 1
        rows = []
        for t in texts:
            v = np.array([1.0 if k in t.lower() else 0.0 for k in KEYWORDS] + [0.05], dtype=np.float32)
            rows.append(v / np.linalg.norm(v))
        return np.array(rows, dtype=np.float32)


@pytest.fixture
def client(monkeypatch, tmp_path):
    embedder = KeywordEmbedder()
    monkeypatch.setattr(srv, "embedder", embedder)
    monkeypatch.setattr(srv, "load_status", "ready")
    monkeypatch.setattr(srv, "load_error", "")
    monkeypatch.setattr(srv, "AUTH_TOKEN", "test-token")
    monkeypatch.setattr(srv, "CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(srv, "vectors", None)
    monkeypatch.setattr(srv, "chunk_metadata", [])
    srv.indexes.clear()
    srv.tag_embeddings.clear()
    with TestClient(srv.app, headers={"X-Riemann-Token": "test-token"}) as c:
        c.embedder = embedder
        yield c


def pdf(tmp_path, name):
    path = tmp_path / name
    path.write_bytes(b"%PDF-" + name.encode())
    return str(path)


def test_clean_and_chunk_text():
    assert srv.clean_text("This   is \n a \t test.") == "This is a test."
    chunks = srv.chunk_text("word1 word2 word3 word4 word5 word6", 1, 3, 1)
    assert [c["text"] for c in chunks] == ["word1 word2 word3", "word3 word4 word5", "word5 word6"]


def test_index_asks_for_text_then_indexes_the_pages_sent(client, tmp_path):
    path = pdf(tmp_path, "a.pdf")
    assert client.post("/index", json={"pdf_path": path}).json() == {"status": "needs_text"}
    reply = client.post("/index", json={"pdf_path": path, "pages": ["alpha one", "", "alpha three"]})
    assert reply.json() == {"status": "success", "chunks_indexed": 2}
    hits = client.post("/search", json={"query": "alpha", "pdf_path": path}).json()
    assert sorted(h["page"] for h in hits) == [1, 3]


def test_a_cached_document_needs_no_text(client, tmp_path):
    path = pdf(tmp_path, "a.pdf")
    client.post("/index", json={"pdf_path": path, "pages": ["alpha"]})
    srv.indexes.clear()
    assert client.post("/index", json={"pdf_path": path}).json()["status"] == "success"


def test_missing_file_empty_text_and_bad_overlap(client, tmp_path):
    assert client.post("/index", json={"pdf_path": str(tmp_path / "gone.pdf")}).status_code == 404
    path = pdf(tmp_path, "a.pdf")
    assert client.post("/index", json={"pdf_path": path, "pages": ["  ", ""]}).status_code == 400
    bad = client.post("/index", json={"pdf_path": path, "chunk_size": 10, "chunk_overlap": 10})
    assert bad.status_code == 422


def test_search_without_index(client):
    assert client.post("/search", json={"query": "q"}).status_code == 400


def test_each_pdf_keeps_its_own_index(client, tmp_path):
    a, b = pdf(tmp_path, "a.pdf"), pdf(tmp_path, "b.pdf")
    client.post("/index", json={"pdf_path": a, "pages": ["alpha text"]})
    client.post("/index", json={"pdf_path": b, "pages": ["beta text"]})
    assert [r["text"] for r in client.post("/search", json={"query": "alpha", "pdf_path": a}).json()] == ["alpha text"]
    assert [r["text"] for r in client.post("/search", json={"query": "beta", "pdf_path": b}).json()] == ["beta text"]
    assert client.post("/search", json={"query": "q", "pdf_path": str(tmp_path / "c.pdf")}).status_code == 400
    assert [r["text"] for r in client.post("/search", json={"query": "beta"}).json()] == ["beta text"]


def test_token_is_required_when_set(client, monkeypatch):
    monkeypatch.setattr(srv, "AUTH_TOKEN", "secret")
    assert client.post("/search", json={"query": "q"}).status_code == 403
    assert client.post("/search", json={"query": "q"}, headers={"X-Riemann-Token": "wrong"}).status_code == 403
    assert client.post("/search", json={"query": "q"}, headers={"X-Riemann-Token": "secret"}).status_code == 400


def test_tags_come_from_the_request(client):
    reply = client.post("/tag", json={"text_chunk": "topology", "tags": ["Topology", "Garden"], "threshold": 0.5})
    assert reply.json() == {"tags": ["Topology"]}
    assert ("Topology", "Garden") in srv.tag_embeddings


def test_library_search_lists_papers_without_vectors_and_caches(client, tmp_path):
    a, b = pdf(tmp_path, "a.pdf"), pdf(tmp_path, "b.pdf")
    gone = str(tmp_path / "gone.pdf")
    first = client.post("/search_library", json={"query": "attention", "pdf_paths": [a, b, gone]}).json()
    assert first["needs_text"] == [a, b] and first["skipped"] == [gone] and first["results"] == []
    client.post("/index", json={"pdf_path": a, "pages": ["attention heads"]})
    client.post("/index", json={"pdf_path": b, "pages": ["garden roses"]})
    assert len(list((tmp_path / "cache").glob("*.npy"))) == 2

    srv.indexes.clear()
    calls = client.embedder.calls
    again = client.post("/search_library", json={"query": "attention", "pdf_paths": [a, b]}).json()
    assert [r["pdf_path"] for r in again["results"]] == [a]
    assert again["searched"] == 2 and again["needs_text"] == []
    assert client.embedder.calls == calls + 2


def test_endpoints_wait_while_the_model_loads_and_report_a_failure(client, monkeypatch):
    monkeypatch.setattr(srv, "load_status", "loading: downloading the search model (40% of 86 MB)")
    reply = client.post("/search", json={"query": "q"})
    assert reply.status_code == 503 and reply.json()["detail"].startswith("loading")
    monkeypatch.setattr(srv, "load_status", "failed")
    monkeypatch.setattr(srv, "load_error", "OSError: no network")
    reply = client.post("/tag", json={"text_chunk": "x"})
    assert reply.status_code == 500 and "no network" in reply.json()["detail"]


def test_websocket_endpoint_is_gone(client):
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/ai") as websocket:
            websocket.receive_text()


def test_model_download_rejects_a_file_with_the_wrong_hash(tmp_path, monkeypatch):
    class Reply(io.BytesIO):
        headers = {"Content-Length": "8"}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    seen = []
    monkeypatch.setattr(srv.urllib.request, "urlopen", lambda url, timeout: (seen.append(url), Reply(b"tampered"))[1])
    with pytest.raises(RuntimeError, match="SHA-256"):
        srv.ensure_model(str(tmp_path))
    assert seen and srv.MODEL_COMMIT in seen[0]
    assert not any(p.name.endswith((".onnx", ".json", ".part")) for p in tmp_path.rglob("*") if p.is_file())


@pytest.mark.skipif(not os.environ.get("RIEMANN_TEST_AI_MODELS"), reason="set RIEMANN_TEST_AI_MODELS to a folder with the model")
def test_onnx_embeddings_rank_related_text_first():
    model_dir = os.environ["RIEMANN_TEST_AI_MODELS"]
    srv.ensure_model(model_dir)
    emb = srv.OnnxEmbedder(model_dir)
    vecs = emb.encode(["Transformers use self-attention.", "Roses need sunlight and water.", "attention mechanism in neural networks"])
    assert vecs.shape == (3, 384)
    assert np.allclose(np.linalg.norm(vecs, axis=1), 1, atol=1e-5)
    assert vecs[2] @ vecs[0] > vecs[2] @ vecs[1] + 0.2


def test_reader_indexes_a_password_protected_pdf_by_sending_its_text(qapp, qtbot, tmp_path, monkeypatch):
    import socket
    import threading
    import time

    import pikepdf
    import uvicorn

    from riemann.core import ai_engine
    from riemann.ui.reader.tab import ReaderTab

    src = os.path.join(os.path.dirname(__file__), "..", "..", "benchmarks", "corpus", "attention.pdf")
    if not os.path.exists(src):
        pytest.skip("benchmark corpus not downloaded")
    locked = str(tmp_path / "locked.pdf")
    with pikepdf.open(src) as pdf:
        pdf.save(locked, encryption=pikepdf.Encryption(owner="owner", user="secret"))

    monkeypatch.setattr(srv, "embedder", KeywordEmbedder())
    monkeypatch.setattr(srv, "load_status", "ready")
    monkeypatch.setattr(srv, "AUTH_TOKEN", "tok")
    monkeypatch.setattr(srv, "CACHE_DIR", "")
    srv.indexes.clear()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(srv.app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    monkeypatch.setattr(ai_engine, "ensure_started", lambda: (port, "tok"))

    tab = ReaderTab()
    qtbot.addWidget(tab)
    try:
        tab.load_document(locked, password="secret")
        end = time.time() + 20
        while time.time() < end and tab.current_doc is None:
            qapp.processEvents()
            time.sleep(0.01)
        assert tab.current_doc is not None
        tab.ai_search("attention")
        end = time.time() + 30
        while time.time() < end and not tab.ai_results:
            qapp.processEvents()
            time.sleep(0.01)
        assert tab.ai_results, "no results came back"
        assert locked in srv.indexes
        assert all("attention" in r["text"].lower() for r in tab.ai_results)
    finally:
        tab.cleanup()
        tab.deleteLater()
        qapp.processEvents()
        server.should_exit = True


def test_library_search_reads_unseen_papers_and_skips_unreadable_ones(tmp_path, monkeypatch):
    import socket
    import threading

    import pikepdf
    import uvicorn

    from riemann.core import ai_engine

    src = os.path.join(os.path.dirname(__file__), "..", "..", "benchmarks", "corpus", "attention.pdf")
    if not os.path.exists(src):
        pytest.skip("benchmark corpus not downloaded")
    locked = str(tmp_path / "locked.pdf")
    with pikepdf.open(src) as pdf:
        pdf.save(locked, encryption=pikepdf.Encryption(owner="o", user="u"))
    monkeypatch.setattr(srv, "embedder", KeywordEmbedder())
    monkeypatch.setattr(srv, "load_status", "ready")
    monkeypatch.setattr(srv, "AUTH_TOKEN", "tok")
    monkeypatch.setattr(srv, "CACHE_DIR", str(tmp_path / "cache"))
    srv.indexes.clear()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(srv.app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    monkeypatch.setattr(ai_engine, "ensure_started", lambda: (port, "tok"))
    try:
        data = ai_engine.search_library("attention", [os.path.abspath(src), locked], top_k=5)
    finally:
        server.should_exit = True
    assert data["results"] and all(r["pdf_path"] == os.path.abspath(src) for r in data["results"])
    assert data["skipped"] == [locked]
    assert data["needs_text"] == []


@pytest.mark.skipif(not os.environ.get("RIEMANN_TEST_AI_MODELS"), reason="set RIEMANN_TEST_AI_MODELS to a folder with the model")
def test_the_engine_starts_as_a_child_process_and_loads_the_model(tmp_path, monkeypatch):
    import shutil
    import time

    import requests

    from riemann.core import ai_engine

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    models = tmp_path / "Riemann" / "models" / "all-MiniLM-L6-v2-onnx"
    shutil.copytree(os.environ["RIEMANN_TEST_AI_MODELS"], models)
    try:
        port, token = ai_engine.ensure_started()
        end, status = time.time() + 60, None
        while time.time() < end:
            try:
                status = requests.get(f"http://127.0.0.1:{port}/status", headers={"X-Riemann-Token": token}, timeout=2).json()
                if status["status"] in ("ready", "failed"):
                    break
            except requests.ConnectionError:
                pass
            time.sleep(0.2)
        assert status == {"status": "ready", "error": ""}
        tags = requests.post(
            f"http://127.0.0.1:{port}/tag",
            json={"text_chunk": "gradient descent for neural networks", "tags": ["Machine Learning", "Cooking"]},
            headers={"X-Riemann-Token": token},
            timeout=30,
        ).json()
        assert tags == {"tags": ["Machine Learning"]}
    finally:
        ai_engine.stop()


def test_server_without_a_token_refuses_every_request(client, monkeypatch):
    monkeypatch.setattr(srv, "AUTH_TOKEN", "")
    assert client.get("/status", headers={"X-Riemann-Token": ""}).status_code == 403
    assert not srv.token_ok("")
    assert not srv.token_ok(None)


@pytest.mark.parametrize("env", [{}, {"RIEMANN_AI_PORT": "8080"}, {"RIEMANN_AI_TOKEN": "x"}])
def test_server_refuses_to_start_without_port_and_token(env):
    import subprocess
    import sys

    child_env = {k: v for k, v in os.environ.items() if not k.startswith("RIEMANN_AI_")}
    child_env.pop("LD_LIBRARY_PATH", None)
    child_env.update(env)
    result = subprocess.run(
        [sys.executable, "-m", "riemann.core.ai_server"],
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        env=child_env, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode != 0
    assert "refusing to run" in result.stderr
