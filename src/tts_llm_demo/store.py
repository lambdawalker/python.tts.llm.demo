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


class AssetInUse(ValueError):
    pass


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
        for job in self.list("job"):
            if job["status"] == "running":
                self.update("job", job["id"], status="interrupted")
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

    def settings(self):
        try:
            return self.get("settings", "workspace")
        except KeyError:
            return {"id": "workspace", "delete_clips": False}

    def delete_chat(self, chat_id):
        self.get("chat", chat_id)
        # Keep audio, job provenance and saved voice references independently of chats.
        with self.db:
            for kind in ("message", "run", "playback"):
                for record in self.list(kind):
                    if record.get("chat_id") == chat_id:
                        self.db.execute(
                            "DELETE FROM records WHERE kind=? AND id=?", (kind, record["id"])
                        )
            self.db.execute("DELETE FROM records WHERE kind='chat' AND id=?", (chat_id,))

    def delete_assets(self, identifiers):
        identifiers = list(dict.fromkeys(identifiers))
        protected = {voice["asset_id"] for voice in self.list("voice")}
        # Validate the complete selection before deleting any files.
        for identifier in identifiers:
            self.get("asset", identifier)
            self.audio_path(identifier)
            if identifier in protected:
                raise AssetInUse("Reference audio used by a saved voice cannot be deleted.")
        deleted, failed = [], []
        for identifier in identifiers:
            try:
                self.audio_path(identifier).unlink(missing_ok=True)
            except OSError:
                failed.append(identifier)
            else:
                deleted.append(identifier)
        removed = set(deleted)
        with self.db:
            for identifier in deleted:
                self.db.execute("DELETE FROM records WHERE kind='asset' AND id=?", (identifier,))
            for kind in ("asset", "chat", "playback"):
                for record in self.list(kind):
                    if kind == "playback" and record["asset_id"] in removed:
                        self.db.execute(
                            "DELETE FROM records WHERE kind=? AND id=?", (kind, record["id"])
                        )
                        continue
                    if kind == "asset" and removed.intersection(record.get("clips", [])):
                        record["clips"] = [c for c in record["clips"] if c not in removed]
                    elif kind == "chat" and record.get("last_asset_id") in removed:
                        record["last_asset_id"] = None
                    else:
                        continue
                    self.db.execute(
                        "UPDATE records SET data=? WHERE kind=? AND id=?",
                        (json.dumps(record), kind, record["id"]),
                    )
        return {"deleted": deleted, "failed": failed}

    def close(self):
        self.db.close()
