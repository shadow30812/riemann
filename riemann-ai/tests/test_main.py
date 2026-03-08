import json
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from fastapi.testclient import TestClient

with patch("sentence_transformers.SentenceTransformer"), patch("faiss.IndexFlatIP"):
    import main

client = TestClient(main.app)


@pytest.fixture(autouse=True)
def reset_globals():
    main.model = MagicMock()
    main.model.encode.return_value = np.array([[0.1, 0.9, 0.5]])
    main.vector_index = None
    main.chunk_metadata = []
    main.tag_embeddings = None
    yield


def test_clean_text():
    raw = "This   is \n a \t test."
    assert main.clean_text(raw) == "This is a test."


def test_chunk_text():
    text = "word1 word2 word3 word4 word5 word6"
    chunks = main.chunk_text(text, 1, 3, 1)

    assert len(chunks) == 3
    assert chunks[0]["text"] == "word1 word2 word3"
    assert chunks[1]["text"] == "word3 word4 word5"
    assert chunks[2]["text"] == "word5 word6"


@patch("os.path.exists", return_value=True)
@patch("fitz.open")
@patch("faiss.IndexFlatIP")
def test_index_pdf(mock_faiss, mock_fitz, mock_exists):
    mock_doc = MagicMock()
    mock_page = MagicMock()
    mock_page.get_text.return_value = "Sample text for indexing."
    mock_doc.__len__.return_value = 1
    mock_doc.__getitem__.return_value = mock_page
    mock_fitz.return_value = mock_doc

    mock_index = MagicMock()
    mock_faiss.return_value = mock_index

    response = client.post(
        "/index", json={"pdf_path": "dummy.pdf", "chunk_size": 200, "chunk_overlap": 50}
    )

    assert response.status_code == 200
    assert response.json()["status"] == "success"
    assert main.vector_index is not None
    assert len(main.chunk_metadata) > 0


@patch("os.path.exists", return_value=False)
def test_index_pdf_not_found(mock_exists):
    response = client.post("/index", json={"pdf_path": "missing.pdf"})
    assert response.status_code == 404


def test_search_pdf_no_index():
    response = client.post("/search", json={"query": "test"})
    assert response.status_code == 400


def test_search_pdf_with_index():
    main.vector_index = MagicMock()
    main.vector_index.search.return_value = (np.array([[0.8]]), np.array([[0]]))
    main.chunk_metadata = [{"page": 1, "text": "Sample text."}]

    response = client.post("/search", json={"query": "test query", "top_k": 1})

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["page"] == 1
    assert data[0]["score"] == 0.8


def test_generate_tags():
    main.model.encode.side_effect = [
        np.array([[0.1, 0.1, 0.1]]),
        np.array([[0.1, 0.1, 0.1]]),
    ]

    with patch("numpy.dot", return_value=np.array([[0.9, 0.1, 0.2]])):
        response = client.post(
            "/tag", json={"text_chunk": "Analog circuit design", "threshold": 0.5}
        )

        assert response.status_code == 200
        assert "tags" in response.json()


@patch("os.path.exists", return_value=False)
def test_websocket_index_missing_file(mock_exists):
    with client.websocket_connect("/ws/ai") as websocket:
        websocket.send_text(json.dumps({"action": "index", "pdf_path": "missing.pdf"}))

        progress = websocket.receive_json()
        assert progress["status"] == "progress"

        error = websocket.receive_json()
        assert error["status"] == "error"


def test_websocket_search_no_index():
    with client.websocket_connect("/ws/ai") as websocket:
        websocket.send_text(json.dumps({"action": "search", "query": "test"}))

        error = websocket.receive_json()
        assert error["status"] == "error"
