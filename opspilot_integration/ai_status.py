"""Show real local storage locations/history. Never calls the LLM or changes K8s."""
import argparse
import json
from pathlib import Path

from dotenv import load_dotenv


def main():
    root = Path(__file__).resolve().parent.parent
    load_dotenv(root / ".env", override=False)
    load_dotenv(root / "ai_service/.env", override=False)
    from ai_service.app.incident_history import (
        history_path, recent_history, get_investigation, pending_verified_outcomes,
    )
    from ai_service.app.rag_tools import chroma_path, get_collection, EMBEDDING_MODEL
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--incident", help="Show a journal investigation ID with bounded evidence")
    parser.add_argument("--retry-index", action="store_true", help="Index verified outcomes after an earlier local indexing failure")
    args = parser.parse_args()
    if args.retry_index:
        from opspilot_integration.learning import index_recovered_investigation
        for row in pending_verified_outcomes():
            index_recovered_investigation(row)
            print("Indexed verified outcome:", row["investigation_id"])
    print("Incident journal:", history_path())
    print("Vector DB:", chroma_path())
    print("Embedding model:", EMBEDDING_MODEL, "(local CPU; not Groq/OpenAI embeddings)")
    try:
        collection = get_collection()
        print("Chroma collection:", collection.name, "| actual records:", collection.count())
    except Exception as error:
        print("Chroma not ready:", type(error).__name__)
    if args.incident:
        row = get_investigation(args.incident)
        if not row:
            raise SystemExit("Investigation not found. No historical data was invented.")
        print(json.dumps(row, indent=2, ensure_ascii=False))
    else:
        rows = recent_history(25)
        print("Recent journal entries:", len(rows), "(maximum: 25; includes filtered alert events)")
        for row in rows:
            print(row["investigation_id"], row["stage"], "approval=", row["approval_id"],
                  "outcome=", row["outcome"].get("status", "not executed"), "indexed=", bool(row["indexed"]))
            if row["stage"] == "ai_failed":
                from ai_service.app.incident_history import redact
                print("  Failure code:", redact(row["state"].get("analysis", "unknown"))[:160])
            if row["stage"] in {"ignored_inactive", "verification_unavailable"}:
                from ai_service.app.incident_history import redact
                checks = row["state"].get("alert_verification", {})
                latest = checks.get("after_ai", checks.get("before_ai", {}))
                print("  Monitoring check:", redact(latest.get("reason", "unknown"))[:160])
        if not rows:
            print("No recorded AI investigations yet; manual tests are not fabricated as history.")


if __name__ == "__main__":
    main()
