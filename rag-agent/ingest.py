"""
Loads incident reports and safety guideline documents from ./data,
splits them into chunks, and stores embeddings in a local persistent
ChromaDB collection for the RAG agent to query.

Run once, and again any time you add new documents to ./data:
    python ingest.py

First run needs internet access — ChromaDB downloads a small embedding
model (all-MiniLM-L6-v2) the first time it's used.
"""

import glob
import os

import chromadb

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
DB_DIR = os.path.join(os.path.dirname(__file__), "chroma_db")
COLLECTION_NAME = "safenet_incidents"

CHUNK_SIZE = 500
CHUNK_OVERLAP = 50


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP):
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start += chunk_size - overlap
    return chunks


def main():
    client = chromadb.PersistentClient(path=DB_DIR)

    # Reset the collection each run so re-ingesting doesn't duplicate chunks.
    try:
        client.delete_collection(COLLECTION_NAME)
    except Exception:
        pass
    collection = client.create_collection(COLLECTION_NAME)

    files = sorted(glob.glob(os.path.join(DATA_DIR, "*.txt")))
    if not files:
        print(f"No .txt files found in {DATA_DIR} — add incident reports first.")
        return

    doc_id = 0
    for filepath in files:
        filename = os.path.basename(filepath)
        with open(filepath, "r", encoding="utf-8") as f:
            text = f.read()

        for chunk in chunk_text(text):
            collection.add(
                ids=[f"{filename}_{doc_id}"],
                documents=[chunk],
                metadatas=[{"source": filename}],
            )
            doc_id += 1

    print(f"Ingested {doc_id} chunks from {len(files)} files into '{COLLECTION_NAME}'.")
    print(f"Stored at: {DB_DIR}")


if __name__ == "__main__":
    main()
