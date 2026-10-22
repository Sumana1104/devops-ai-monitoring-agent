
from pathlib import Path

from ai_service.app.rag_tools import (
    generate_embedding,
    get_collection,
)

RUNBOOK_DIR = (
    Path(__file__).resolve().parents[1]
    / "knowledge"
    / "runbooks"
)


def ingest_runbooks():
    """Load runbook documents into ChromaDB."""

    collection = get_collection()
    files = sorted(RUNBOOK_DIR.glob("*.md"))

    if not files:
        raise RuntimeError("No runbooks found")

    for file in files:
        text = file.read_text(encoding="utf-8")

        if not text.strip():
            continue

        category = (
            "cicd" if file.name.startswith("github_")
            else "kubernetes"
        )

        collection.upsert(
            ids=[f"runbook:{file.stem}"],
            documents=[text],
            embeddings=[generate_embedding(text)],
            metadatas=[{
                "incident_id": file.stem,
                "type": "reviewed_runbook",
                "category": category,
                "source": file.name,
            }],
        )

        print(f"Indexed: {file.name}")

    print(f"Total ChromaDB records: {collection.count()}")


if __name__ == "__main__":
    ingest_runbooks()
