"""Small durable workspace. All IDs and paths are application-generated."""

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path


def uid():
    return uuid.uuid4().hex


def now():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, directory: Path):
        self.directory = directory
        self.audio_dir = directory / "audio"
        self.audio_dir.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(directory / "workspace.sqlite", check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS records "
            "(kind TEXT, id TEXT PRIMARY KEY, data TEXT NOT NULL)"
        )
        self.db.commit()
        for run in self.list("run"):
            if run["status"] in {"running", "cancelling"}:
                self.update(
                    "run",
                    run["id"],
                    status="interrupted",
                    stage="Application restarted; work was not retried.",
                )

    def put(self, kind, data):
        self.db.execute(
            "INSERT OR REPLACE INTO records VALUES (?,?,?)",
            (kind, data["id"], json.dumps(data, allow_nan=False)),
        )
        self.db.commit()
        return data

    def get(self, kind, identifier):
        row = self.db.execute(
            "SELECT data FROM records WHERE kind=? AND id=?", (kind, identifier)
        ).fetchone()
        if not row:
            raise KeyError(f"Unknown {kind}")
        return json.loads(row[0])

    def list(self, kind):
        return [
            json.loads(row[0])
            for row in self.db.execute(
                "SELECT data FROM records WHERE kind=? ORDER BY rowid", (kind,)
            )
        ]

    def update(self, kind, identifier, **fields):
        return self.put(kind, {**self.get(kind, identifier), **fields})

    def new_chat(self):
        return self.put(
            "chat",
            dict(
                id=uid(),
                title="New conversation",
                created_at=now(),
                pending=None,
                pending_turn=None,
                turn=0,
            ),
        )

    def message(self, chat_id, role, content):
        return self.put(
            "message", dict(id=uid(), chat_id=chat_id, role=role, content=content, created_at=now())
        )

    def messages(self, chat_id):
        return [m for m in self.list("message") if m["chat_id"] == chat_id]

    def audio_path(self, identifier):
        # Internal metadata is never used as a path supplied by the LLM/browser.
        if len(identifier) != 32 or any(c not in "0123456789abcdef" for c in identifier):
            raise KeyError("Unknown asset")
        return self.audio_dir / f"{identifier}.wav"

    def delete(self, kind, identifier):
        self.db.execute("DELETE FROM records WHERE kind=? AND id=?", (kind, identifier))
        self.db.commit()

    def close(self):
        self.db.close()
