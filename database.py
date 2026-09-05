"""Local cache only; WordPress companion owns durable publishing receipts.
Legacy processed_entries is preserved and still consulted during migration.
"""
import json
import sqlite3
from pathlib import Path

class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, timeout=30)
        self.conn.execute("""CREATE TABLE IF NOT EXISTS news_receipts (
            source_key TEXT PRIMARY KEY, content_hash TEXT NOT NULL,
            receipt TEXT NOT NULL)""")
        self.conn.commit()

    def legacy_post(self, guid):
        exists = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='processed_entries'").fetchone()
        if not exists:
            return None
        row = self.conn.execute("SELECT post_id FROM processed_entries WHERE guid=?", (guid,)).fetchone()
        return row[0] if row else None

    def get(self, source_key):
        row = self.conn.execute("SELECT content_hash, receipt FROM news_receipts WHERE source_key=?",
                                (source_key,)).fetchone()
        return {"content_hash": row[0], **json.loads(row[1])} if row else None

    def save(self, source_key, content_hash, receipt):
        with self.conn:
            self.conn.execute("""INSERT INTO news_receipts VALUES (?, ?, ?)
                ON CONFLICT(source_key) DO UPDATE SET content_hash=excluded.content_hash,
                receipt=excluded.receipt""", (source_key, content_hash, json.dumps(receipt)))

    def close(self):
        self.conn.close()
