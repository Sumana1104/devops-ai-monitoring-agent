
import os

import chromadb
from sentence_transformers import SentenceTransformer


# Persistent local vector database
CHROMA_PATH = os.getenv(
    "OPSPILOT_CHROMA_PATH",
    "./data/chroma"
)

COLLECTION_NAME = "opspilot_incidents"

EMBEDDING_MODEL = "all-MiniLM-L6-v2"

_model = None


def get_embedding_model():
    global _model

    if _model is None:
        _model = SentenceTransformer(EMBEDDING_MODEL)

    return _model


def get_collection():
    client = chromadb.PersistentClient(path=CHROMA_PATH)

    return client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )


def generate_embedding(text):
    model = get_embedding_model()
    return model.encode(text).tolist()


def store_incident(
    incident_id,
    description,
    root_cause,
    resolution,
):
    if not all([
        incident_id,
        description,
        root_cause,
        resolution,
    ]):
        raise ValueError("Incident fields cannot be empty")

    document = (
        f"Incident: {description}\n"
        f"Root cause: {root_cause}\n"
        f"Verified resolution: {resolution}"
    )

    embedding = generate_embedding(document)

    collection = get_collection()

    collection.upsert(
        ids=[incident_id],
        embeddings=[embedding],
        documents=[document],
        metadatas=[{
            "incident_id": incident_id,
            "type": "verified_incident",
        }],
    )


def retrieve_similar_incidents(query, top_k=3):
    collection = get_collection()

    if collection.count() == 0:
        return []

    embedding = generate_embedding(query)

    results = collection.query(
        query_embeddings=[embedding],
        n_results=min(top_k, collection.count()),
        include=["documents", "metadatas", "distances"],
        where={"type": "verified_incident"},
    )

    matches = []

    for index, document in enumerate(results["documents"][0]):
        matches.append({
            "document": document,
            "metadata": results["metadatas"][0][index],
            "distance": results["distances"][0][index],
        })

    return matches
