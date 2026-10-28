"""Local semantic RAG: Chroma + MiniLM. Imports/downloads are lazy and explicit."""
import os
from pathlib import Path
import threading

from ai_service.app.incident_history import redact

ROOT = Path(__file__).resolve().parents[2]
COLLECTION_NAME = "opspilot_incidents"
EMBEDDING_MODEL = "all-MiniLM-L6-v2"
_model = None
_lock = threading.Lock()


def chroma_path():
    path = Path(os.getenv("OPSPILOT_CHROMA_PATH", "data/chroma"))
    return path if path.is_absolute() else ROOT / path


def get_embedding_model():
    global _model
    with _lock:
        if _model is None:
            from sentence_transformers import SentenceTransformer
            _model = SentenceTransformer(EMBEDDING_MODEL, device="cpu")
    return _model


def get_collection():
    import chromadb
    return chromadb.PersistentClient(path=str(chroma_path())).get_or_create_collection(
        name=COLLECTION_NAME, metadata={"hnsw:space": "cosine"}, embedding_function=None,
    )


def generate_embedding(text):
    return get_embedding_model().encode(redact(text)).tolist()


def store_incident(incident_id, description, root_cause, resolution, *, verified=False):
    """Only verified recovery outcomes may be labelled verified_incident."""
    if not verified:
        raise ValueError("A verified recovery outcome is required for incident indexing")
    if not all((incident_id, description, root_cause, resolution)):
        raise ValueError("Incident fields cannot be empty")
    document = redact(
        f"Incident: {description}\nAI hypothesis (not independently proven): {root_cause}\n"
        f"Verified recovery outcome: {resolution}"
    )[:8000]
    get_collection().upsert(
        ids=[incident_id], embeddings=[generate_embedding(document)], documents=[document],
        metadatas=[{"incident_id": incident_id, "type": "verified_incident",
                    "source": "human-approved demo rollout; Kubernetes recovery verified"}],
    )


def retrieve_similar_incidents(query, top_k=3):
    collection = get_collection()
    count = collection.count()
    if not count:
        return []
    results = collection.query(
        query_embeddings=[generate_embedding(query)], n_results=min(max(1, int(top_k)), count),
        include=["documents", "metadatas", "distances"],
        where={"type": {"$in": ["verified_incident", "reviewed_runbook", "runbook"]}},
    )
    matches = []
    for index, document in enumerate((results.get("documents") or [[]])[0]):
        distance = results["distances"][0][index]
        if distance > 0.8:
            continue
        matches.append({"document": document, "metadata": results["metadatas"][0][index] or {},
                        "distance": distance})
    return matches
