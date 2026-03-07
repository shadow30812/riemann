import json
import os
import re
from contextlib import asynccontextmanager

import faiss
import fitz
import numpy as np
import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer

# --- Global State ---
# We keep the model, index, and metadata in memory for fast access.
MODEL_NAME = "all-MiniLM-L6-v2"
model = None
vector_index = None
chunk_metadata = []  # List to store dicts: {"page": int, "text": str}


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
tag_embeddings = None


# --- Pydantic Models ---
class IndexRequest(BaseModel):
    pdf_path: str
    chunk_size: int = 200
    chunk_overlap: int = 50


class SearchRequest(BaseModel):
    query: str
    top_k: int = 5


class SearchResult(BaseModel):
    page: int
    text: str
    score: float


class TagRequest(BaseModel):
    text_chunk: str
    threshold: float = 0.25


# --- Helper Functions ---
def clean_text(text: str) -> str:
    """Removes excessive newlines and spaces."""
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def chunk_text(text: str, page_num: int, chunk_size: int, overlap: int):
    """Splits text into sliding-window chunks."""
    words = text.split()
    chunks = []
    if not words:
        return chunks

    for i in range(0, len(words), chunk_size - overlap):
        chunk = " ".join(words[i : i + chunk_size])
        if chunk:
            chunks.append({"page": page_num, "text": chunk})
    return chunks


# --- Lifespan (Startup/Shutdown) ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    global model
    print(f"Loading embedding model '{MODEL_NAME}' onto CPU...")
    model = SentenceTransformer(MODEL_NAME, device="cpu")
    print("Model loaded successfully.")
    yield
    print("Shutting down AI service...")


# --- FastAPI App ---
app = FastAPI(title="Riemann AI Sidecar", lifespan=lifespan)


@app.post("/index")
async def index_pdf(req: IndexRequest):
    global vector_index, chunk_metadata, model

    if not os.path.exists(req.pdf_path):
        raise HTTPException(status_code=404, detail="PDF file not found.")

    try:
        print(f"Opening PDF: {req.pdf_path}")
        doc = fitz.open(req.pdf_path)
        all_chunks = []

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
async def search_pdf(req: SearchRequest):
    global vector_index, chunk_metadata, model

    if vector_index is None or not chunk_metadata:
        raise HTTPException(status_code=400, detail="No PDF is currently indexed.")

    try:
        query_embedding = model.encode(
            [req.query], convert_to_numpy=True, normalize_embeddings=True
        )

        distances, indices = vector_index.search(query_embedding, req.top_k)
        results = []

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
async def ai_websocket(websocket: WebSocket):
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
                all_chunks = []
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

                results = []
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
async def generate_tags(req: TagRequest):
    global tag_embeddings, model
    if model is None:
        raise HTTPException(status_code=503, detail="Model not loaded yet.")

    if tag_embeddings is None:
        # Compute these once and cache them
        tag_embeddings = model.encode(
            AVAILABLE_TAGS, convert_to_numpy=True, normalize_embeddings=True
        )

    try:
        chunk_emb = model.encode(
            [req.text_chunk], convert_to_numpy=True, normalize_embeddings=True
        )
        similarities = np.dot(chunk_emb, tag_embeddings.T)[0]

        assigned_tags = []
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
    # Run server locally. In production, Riemann main app will spawn this.
    uvicorn.run(app, host="127.0.0.1", port=8080, log_level="info")
