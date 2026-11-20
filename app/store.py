from contextlib import contextmanager
import json
import sqlite3

from app.errors import BusinessError
from app.manifest import canonical


class Ledger:
    def __init__(self, path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS sources (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
            """)

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def source(self, identifier):
        with self.connect() as conn:
            row = conn.execute(
                "SELECT payload FROM sources WHERE id = ?", (identifier,)
            ).fetchone()
        if row is None:
            raise BusinessError("SOURCE_NOT_FOUND", "未找到数据来源", 404)
        return json.loads(row[0])

    def put_source(self, identifier, payload):
        return self._put("sources", identifier, payload, "SOURCE_CONFLICT", "来源摘要冲突")

    def run(self, identifier):
        with self.connect() as conn:
            row = conn.execute(
                "SELECT payload FROM runs WHERE id = ?", (identifier,)
            ).fetchone()
        if row is None:
            raise BusinessError("RUN_NOT_FOUND", "未找到归档记录", 404)
        return json.loads(row[0])

    def put_run(self, payload):
        return self._put("runs", payload["id"], payload, "RUN_CONFLICT", "归档摘要冲突")

    def _put(self, table, identifier, payload, code, message):
        """Insert once; an existing row is reused only if the bytes agree.

        Recovery must never silently replace an archived record with a
        different one that happens to share its identifier, so a mismatch is
        reported and the stored evidence is left untouched.
        """
        body = canonical(payload)
        with self.connect() as conn:
            cursor = conn.execute(
                f"INSERT OR IGNORE INTO {table} VALUES (?, ?)",
                (identifier, body),
            )
            if cursor.rowcount:
                return False
            row = conn.execute(
                f"SELECT payload FROM {table} WHERE id = ?", (identifier,)
            ).fetchone()
        if row is not None and row[0] != body:
            raise BusinessError(code, message, 409)
        return True
