"""
Riemann AI Sidecar Engine.

This module provides a local FastAPI-based backend for the Riemann desktop application.
It exposes REST and WebSocket endpoints for document indexing, semantic search,
and automatic tagging using FAISS and SentenceTransformers. All inference runs
locally to ensure data privacy and offline capability.
"""

import json
import os
import re
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

import faiss
import fitz
import numpy as np
import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer

MODEL_NAME = "all-MiniLM-L6-v2"

# Global state for the AI Sidecar
model: SentenceTransformer | None = None
vector_index: faiss.IndexFlatIP | None = None
chunk_metadata: list[dict[str, Any]] = []

AVAILABLE_TAGS = [
    "Analog Circuits",
    "Signal Processing",
    "Optimization Theory",
    "Competitive Programming",
    "Economics",
    "Machine Learning",
    "Probability Theory",
    "Electrical Machines",
    "Artificial Intelligence",
    "Digital Circuits",
]
tag_embeddings: np.ndarray | None = None


class IndexRequest(BaseModel):
    """Payload for PDF indexing requests."""

    pdf_path: str
    chunk_size: int = 200
    chunk_overlap: int = 50


class SearchRequest(BaseModel):
    """Payload for semantic search requests."""

    query: str
    top_k: int = 5


class SearchResult(BaseModel):
    """Structure of a single semantic search result."""

    page: int
    text: str
    score: float


class TagRequest(BaseModel):
    """Payload for automated tagging of text chunks."""

    text_chunk: str
    threshold: float = 0.25


def clean_text(text: str) -> str:
    """
    Cleans raw text extracted from a PDF by normalizing whitespace.

    Args:
        text (str): The raw text to clean.

    Returns:
        str: The cleaned, single-spaced string.
    """
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def chunk_text(
    text: str, page_num: int, chunk_size: int, overlap: int
) -> list[dict[str, Any]]:
    """
    Splits text into sliding-window chunks for vector embedding.

    Args:
        text (str): The text to be chunked.
        page_num (int): The 1-based page number from which the text was extracted.
        chunk_size (int): The maximum number of words per chunk.
        overlap (int): The number of overlapping words between consecutive chunks.

    Returns:
        list[dict[str, Any]]: A list of dictionaries containing chunk text and page metadata.
    """
    words = text.split()
    chunks: list[dict[str, Any]] = []

    if not words:
        return chunks

    for i in range(0, len(words), chunk_size - overlap):
        chunk = " ".join(words[i : i + chunk_size])
        if chunk:
            chunks.append({"page": page_num, "text": chunk})

    return chunks


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Manages the lifecycle of the FastAPI application.
    Loads the sentence transformer model into memory on startup and cleans up on shutdown.

    Args:
        app (FastAPI): The FastAPI application instance.
    """
    global model
    print(f"Loading embedding model '{MODEL_NAME}' onto CPU...")
    model = SentenceTransformer(MODEL_NAME, device="cpu")
    print("Model loaded successfully.")

    yield

    print("Shutting down AI service...")


app = FastAPI(title="Riemann AI Sidecar", lifespan=lifespan)


@app.post("/index")
async def index_pdf(req: IndexRequest) -> dict[str, Any]:
    """
    Reads a PDF, extracts its text, chunks it, and builds a FAISS vector index.

    Args:
        req (IndexRequest): The configuration for the indexing operation.

    Returns:
        dict[str, Any]: A status dictionary including the number of chunks indexed.

    Raises:
        HTTPException: If the PDF cannot be found, read, or contains no text.
    """
    global vector_index, chunk_metadata, model

    if not os.path.exists(req.pdf_path):
        raise HTTPException(status_code=404, detail="PDF file not found.")

    try:
        print(f"Opening PDF: {req.pdf_path}")
        doc = fitz.open(req.pdf_path)
        all_chunks: list[dict[str, Any]] = []

        for page_num in range(len(doc)):
            page = doc[page_num]
            text = clean_text(page.get_text("text"))
            if text:
                page_chunks = chunk_text(
                    text, page_num + 1, req.chunk_size, req.chunk_overlap
                )
                all_chunks.extend(page_chunks)
        doc.close()

        if not all_chunks:
            raise HTTPException(
                status_code=400, detail="No readable text found in PDF."
            )

        print(f"Extracted {len(all_chunks)} chunks. Generating embeddings...")
        texts = [c["text"] for c in all_chunks]
        embeddings = model.encode(
            texts, convert_to_numpy=True, normalize_embeddings=True
        )

        embedding_dim = embeddings.shape[1]
        vector_index = faiss.IndexFlatIP(embedding_dim)
        vector_index.add(embeddings)
        chunk_metadata = all_chunks

        return {"status": "success", "chunks_indexed": len(all_chunks)}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/search", response_model=list[SearchResult])
async def search_pdf(req: SearchRequest) -> list[SearchResult]:
    """
    Performs a semantic search against the currently indexed PDF.

    Args:
        req (SearchRequest): The search query and configuration.

    Returns:
        list[SearchResult]: The top matches exceeding the similarity threshold.

    Raises:
        HTTPException: If no PDF is currently indexed or if the search fails.
    """
    global vector_index, chunk_metadata, model

    if vector_index is None or not chunk_metadata:
        raise HTTPException(status_code=400, detail="No PDF is currently indexed.")

    try:
        query_embedding = model.encode(
            [req.query], convert_to_numpy=True, normalize_embeddings=True
        )
        distances, indices = vector_index.search(query_embedding, req.top_k)

        results: list[SearchResult] = []
        for i in range(req.top_k):
            idx = indices[0][i]
            score = float(distances[0][i])
            if idx != -1 and idx < len(chunk_metadata) and score >= 0.35:
                meta = chunk_metadata[idx]
                results.append(
                    SearchResult(page=meta["page"], text=meta["text"], score=score)
                )

        return results

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.websocket("/ws/ai")
async def ai_websocket(websocket: WebSocket) -> None:
    """
    WebSocket endpoint for persistent, bidirectional communication with the Riemann frontend.
    Handles 'index' and 'search' actions while emitting progress updates.

    Args:
        websocket (WebSocket): The active WebSocket connection.
    """
    await websocket.accept()
    global vector_index, chunk_metadata, model

    try:
        while True:
            data = await websocket.receive_text()
            req = json.loads(data)
            action = req.get("action")

            if action == "index":
                pdf_path = req.get("pdf_path")
                chunk_size = req.get("chunk_size", 200)
                chunk_overlap = req.get("chunk_overlap", 50)

                await websocket.send_text(
                    json.dumps(
                        {"status": "progress", "msg": f"Opening PDF: {pdf_path}"}
                    )
                )

                if not os.path.exists(pdf_path):
                    await websocket.send_text(
                        json.dumps({"status": "error", "msg": "PDF file not found."})
                    )
                    continue

                doc = fitz.open(pdf_path)
                all_chunks: list[dict[str, Any]] = []
                for page_num in range(len(doc)):
                    page = doc[page_num]
                    text = clean_text(page.get_text("text"))
                    if text:
                        all_chunks.extend(
                            chunk_text(text, page_num + 1, chunk_size, chunk_overlap)
                        )
                doc.close()

                await websocket.send_text(
                    json.dumps(
                        {
                            "status": "progress",
                            "msg": f"Extracted {len(all_chunks)} chunks. Generating embeddings...",
                        }
                    )
                )

                texts = [c["text"] for c in all_chunks]
                embeddings = model.encode(
                    texts, convert_to_numpy=True, normalize_embeddings=True
                )

                embedding_dim = embeddings.shape[1]
                vector_index = faiss.IndexFlatIP(embedding_dim)
                vector_index.add(embeddings)
                chunk_metadata = all_chunks

                await websocket.send_text(
                    json.dumps(
                        {
                            "status": "success",
                            "msg": "Indexing complete",
                            "chunks": len(all_chunks),
                        }
                    )
                )

            elif action == "search":
                query = req.get("query")
                top_k = req.get("top_k", 5)

                if vector_index is None or not chunk_metadata:
                    await websocket.send_text(
                        json.dumps({"status": "error", "msg": "No PDF indexed."})
                    )
                    continue

                query_embedding = model.encode(
                    [query], convert_to_numpy=True, normalize_embeddings=True
                )
                distances, indices = vector_index.search(query_embedding, top_k)

                results: list[dict[str, Any]] = []
                for i in range(top_k):
                    idx = indices[0][i]
                    score = float(distances[0][i])
                    if idx != -1 and idx < len(chunk_metadata) and score >= 0.35:
                        meta = chunk_metadata[idx]
                        results.append(
                            {"page": meta["page"], "text": meta["text"], "score": score}
                        )

                await websocket.send_text(
                    json.dumps({"status": "results", "data": results})
                )

    except WebSocketDisconnect:
        print("WebSocket client disconnected")
    except Exception as e:
        await websocket.send_text(json.dumps({"status": "error", "msg": str(e)}))


@app.post("/tag")
async def generate_tags(req: TagRequest) -> dict[str, list[str]]:
    """
    Evaluates a chunk of text and assigns it the most relevant predefined tags.

    Args:
        req (TagRequest): The payload containing the text chunk and similarity threshold.

    Returns:
        dict[str, list[str]]: A dictionary containing up to 3 assigned tags.

    Raises:
        HTTPException: If the model is not loaded or tag generation fails.
    """
    global tag_embeddings, model

    if model is None:
        raise HTTPException(status_code=503, detail="Model not loaded yet.")

    if tag_embeddings is None:
        tag_embeddings = model.encode(
            AVAILABLE_TAGS, convert_to_numpy=True, normalize_embeddings=True
        )

    try:
        chunk_emb = model.encode(
            [req.text_chunk], convert_to_numpy=True, normalize_embeddings=True
        )
        similarities = np.dot(chunk_emb, tag_embeddings.T)[0]

        assigned_tags: list[dict[str, Any]] = []
        for idx, score in enumerate(similarities):
            if score > req.threshold:
                assigned_tags.append(
                    {"tag": AVAILABLE_TAGS[idx], "score": float(score)}
                )

        assigned_tags = sorted(assigned_tags, key=lambda x: x["score"], reverse=True)
        return {"tags": [t["tag"] for t in assigned_tags[:3]]}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8080, log_level="info")
