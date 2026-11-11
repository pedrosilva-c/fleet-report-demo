"""Persistência em SQLite: lotes de importação e eventos.

- Um mesmo arquivo (mesmo SHA-256) não é importado duas vezes.
- Um evento idêntico já gravado por outro lote é rejeitado como duplicata.
- Um lote pode ser revertido: seus eventos são removidos, o registro do lote
  permanece com status "revertido" para auditoria.
"""
from __future__ import annotations

import hashlib
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

from .importer import ParseResult, Rejection

SCHEMA = """
CREATE TABLE IF NOT EXISTS imports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL,
    sha256 TEXT NOT NULL UNIQUE,
    imported_at TEXT NOT NULL,
    accepted INTEGER NOT NULL,
    rejected INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'ativo' CHECK (status IN ('ativo', 'revertido'))
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    import_id INTEGER NOT NULL REFERENCES imports(id),
    ts TEXT NOT NULL,
    vehicle TEXT NOT NULL,
    driver TEXT NOT NULL,
    event_type TEXT NOT NULL,
    value REAL NOT NULL,
    UNIQUE (ts, vehicle, event_type, value)
);
CREATE INDEX IF NOT EXISTS idx_events_day ON events (substr(ts, 1, 10));
"""


class DuplicateFileError(Exception):
    def __init__(self, import_id: int):
        super().__init__(f"Este arquivo já foi importado (lote {import_id}).")
        self.import_id = import_id


class Store:
    def __init__(self, path: Path | str):
        self.path = str(path)
        with closing(self._connect()) as db:
            db.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        return db

    def save_import(self, filename: str, content: bytes, parsed: ParseResult) -> dict:
        digest = hashlib.sha256(content).hexdigest()
        with closing(self._connect()) as db, db:
            row = db.execute("SELECT id FROM imports WHERE sha256 = ?", (digest,)).fetchone()
            if row:
                raise DuplicateFileError(row["id"])
            cur = db.execute(
                "INSERT INTO imports (filename, sha256, imported_at, accepted, rejected) VALUES (?, ?, ?, 0, 0)",
                (filename, digest, datetime.now().isoformat(timespec="seconds")))
            import_id = cur.lastrowid
            rejected = list(parsed.rejected)
            accepted = 0
            for ev in parsed.accepted:
                try:
                    db.execute(
                        "INSERT INTO events (import_id, ts, vehicle, driver, event_type, value) VALUES (?, ?, ?, ?, ?, ?)",
                        (import_id, ev.timestamp.isoformat(sep=" ", timespec="minutes"), ev.vehicle, ev.driver, ev.event_type, ev.value))
                    accepted += 1
                except sqlite3.IntegrityError:
                    rejected.append(Rejection(0, "Evento já importado por outro lote.", {
                        "data_hora": ev.timestamp.isoformat(sep=" ", timespec="minutes"), "veiculo": ev.vehicle,
                        "tipo_evento": ev.event_type, "valor": ev.value}))
            db.execute("UPDATE imports SET accepted = ?, rejected = ? WHERE id = ?", (accepted, len(rejected), import_id))
        return {
            "import_id": import_id, "filename": filename, "accepted": accepted,
            "rejected": [{"linha": r.line or None, "motivo": r.reason, "dados": r.raw} for r in rejected],
        }

    def revert_import(self, import_id: int) -> int:
        with closing(self._connect()) as db, db:
            row = db.execute("SELECT status FROM imports WHERE id = ?", (import_id,)).fetchone()
            if row is None:
                raise KeyError(import_id)
            if row["status"] == "revertido":
                return 0
            removed = db.execute("DELETE FROM events WHERE import_id = ?", (import_id,)).rowcount
            db.execute("UPDATE imports SET status = 'revertido' WHERE id = ?", (import_id,))
        return removed

    def list_imports(self) -> list[dict]:
        with closing(self._connect()) as db:
            return [dict(r) for r in db.execute("SELECT * FROM imports ORDER BY id DESC")]

    def events_on(self, day: str) -> list[sqlite3.Row]:
        with closing(self._connect()) as db:
            return db.execute(
                "SELECT ts, vehicle, driver, event_type, value FROM events WHERE substr(ts, 1, 10) = ? ORDER BY ts",
                (day,)).fetchall()

    def days(self) -> list[str]:
        with closing(self._connect()) as db:
            return [r[0] for r in db.execute("SELECT DISTINCT substr(ts, 1, 10) FROM events ORDER BY 1")]
