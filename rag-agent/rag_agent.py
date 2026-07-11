"""
SafeNet RAG agent.

Given a description of the current situation (typically a compound risk
alert coming from the risk engine), this retrieves the most relevant past
incident reports and safety guidelines from ChromaDB, then asks Gemini
2.0 Flash to turn that into a short, grounded warning — the "this exact
combination caused an accident before, be careful" message from the
pitch.

Requires a Gemini API key. Get one at https://aistudio.google.com/apikey
and either export it as an environment variable, or drop it into a .env
file next to this script (copy .env.example -> .env and fill it in).

Run ingest.py first — this script errors clearly if no data has been
ingested yet.
"""

import os
import time

import chromadb
from dotenv import load_dotenv
from google import genai
from google.genai.errors import ClientError

load_dotenv(dotenv_path=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

DB_DIR = os.path.join(os.path.dirname(__file__), "chroma_db")
COLLECTION_NAME = "safenet_incidents"
GEMINI_MODEL = "gemini-2.5-flash"


def _get_collection():
    client = chromadb.PersistentClient(path=DB_DIR)
    try:
        return client.get_collection(COLLECTION_NAME)
    except Exception as e:
        raise RuntimeError(
            "No ingested data found. Run ingest.py first to build the "
            "incident database."
        ) from e


def retrieve_similar_incidents(situation: str, n_results: int = 3):
    """
    Returns the most relevant chunks (with source filenames) for a given
    situation description. No LLM call — useful on its own for a
    "related incidents" panel in the dashboard even without an API key.
    """
    collection = _get_collection()
    results = collection.query(query_texts=[situation], n_results=n_results)

    matches = []
    for doc, meta in zip(results["documents"][0], results["metadatas"][0]):
        matches.append({"source": meta["source"], "excerpt": doc.strip()})
    return matches


def generate_warning(situation: str, n_results: int = 3, retry_waits=(16, 30, 60)) -> dict:
    """
    Full RAG pipeline: retrieve relevant past incidents, then ask Gemini
    to synthesize a short warning grounded in them.

    retry_waits controls how long (in seconds) to wait between retries on
    a transient rate limit. Defaults to a patient sequence for manual
    testing. Pass () for a single fast attempt — e.g. when calling this
    live during a demo, where a 60+ second pause would kill the moment.

    Returns {"warning": str|None, "sources": [...], "error": str|None}.
    If no API key is set, still returns the retrieved sources with a
    clear error message instead of failing outright — the dashboard can
    show "related incidents" even without generation working.
    """
    matches = retrieve_similar_incidents(situation, n_results=n_results)

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return {
            "warning": None,
            "sources": matches,
            "error": "GEMINI_API_KEY not set — showing retrieved incidents only, no generated summary.",
        }

    client = genai.Client(api_key=api_key)

    context = "\n\n".join(f"[{m['source']}]: {m['excerpt']}" for m in matches)
    prompt = (
        "You are a plant safety assistant. Based only on the past incident "
        "excerpts below, write a single short warning (2-3 sentences, plain "
        "language, no headers) for a worker or supervisor about the current "
        "situation. Reference what happened before only if it's genuinely "
        "similar. If nothing below is relevant, say so plainly.\n\n"
        f"Current situation: {situation}\n\n"
        f"Past incident excerpts:\n{context}"
    )

    # Free-tier quota is tight — try a few times with increasing waits
    # rather than giving up after one retry. But if the error says the
    # limit itself is 0, this is a project/billing setup issue, not a
    # transient rate limit — retrying won't help, so fail fast instead
    # of burning time for nothing.
    last_error = None
    for attempt, wait in enumerate([0] + list(retry_waits)):
        if wait:
            time.sleep(wait)
        try:
            response = client.models.generate_content(
                model=GEMINI_MODEL, contents=prompt
            )
            return {"warning": response.text.strip(), "sources": matches, "error": None}
        except ClientError as e:
            last_error = e
            if "limit: 0" in str(e):
                break
            continue

    if last_error and "limit: 0" in str(last_error):
        error_msg = (
            "Gemini API key has zero free-tier quota allocated for this "
            "model/project — this is a setup issue, not a rate limit. "
            "Check https://aistudio.google.com/apikey and confirm the key "
            "belongs to a project with the Gemini API free tier enabled."
        )
    else:
        error_msg = (
            f"Gemini rate limit persisted after {len(retry_waits) + 1} "
            f"attempts — showing retrieved incidents only. ({last_error})"
        )

    return {"warning": None, "sources": matches, "error": error_msg}


if __name__ == "__main__":
    test_situation = (
        "Gas levels rising in zone 4 while a maintenance permit is active "
        "and a shift changeover is approaching."
    )
    result = generate_warning(test_situation)

    print("Situation:", test_situation)
    print("\nWarning:", result["warning"] or f"(not generated: {result['error']})")
    print("\nSources retrieved:")
    for s in result["sources"]:
        print(f" - {s['source']}: {s['excerpt'][:100]}...")
