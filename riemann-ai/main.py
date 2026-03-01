import os
import re
from contextlib import asynccontextmanager

import faiss
import fitz  
import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer

# --- Global State ---
# We keep the model, index, and metadata in memory for fast access.
MODEL_NAME = "all-MiniLM-L6-v2"
model = None
vector_index = None
chunk_metadata = []  # List to store dicts: {"page": int, "text": str}


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
    # Force CPU to prevent massive GPU VRAM spikes and CUDA compilation bloat
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

        # Extract and chunk text per page
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

        # Generate embeddings
        texts = [c["text"] for c in all_chunks]
        embeddings = model.encode(texts, convert_to_numpy=True)

        # Initialize or reset FAISS index
        embedding_dim = embeddings.shape[1]
        vector_index = faiss.IndexFlatL2(embedding_dim)
        vector_index.add(embeddings)

        # Store metadata
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
        # Embed the search query
        query_embedding = model.encode([req.query], convert_to_numpy=True)

        # Search FAISS (returns distances and indices)
        distances, indices = vector_index.search(query_embedding, req.top_k)

        results = []
        for i in range(req.top_k):
            idx = indices[0][i]
            if idx != -1 and idx < len(chunk_metadata):  # Ensure valid index
                meta = chunk_metadata[idx]
                results.append(
                    SearchResult(
                        page=meta["page"],
                        text=meta["text"],
                        score=float(distances[0][i]),
                    )
                )
        return results

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    # Run server locally. In production, Riemann main app will spawn this.
    uvicorn.run(app, host="127.0.0.1", port=8080, log_level="info")
