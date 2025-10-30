from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional


@contextmanager
def get_connection(db_path: Path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.commit()
        conn.close()


def init_db(db_path: Path) -> None:
    with get_connection(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                status TEXT DEFAULT 'pending',
                total_terms INTEGER DEFAULT 0,
                processed_terms INTEGER DEFAULT 0,
                total_products INTEGER DEFAULT 0,
                region_id TEXT,
                region_info TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT REFERENCES sessions(id),
                search_term TEXT,
                found INTEGER,
                data TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_products_session ON products(session_id)"
        )


def insert_session(
    db_path: Path,
    session_id: str,
    total_terms: int,
    region_id: Optional[str],
    region_info: Dict,
) -> None:
    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO sessions (
                id, status, total_terms, processed_terms, total_products, region_id, region_info
            ) VALUES (?, 'active', ?, 0, 0, ?, ?)
            """,
            (
                session_id,
                total_terms,
                region_id,
                json.dumps(region_info),
            ),
        )


def update_session_progress(
    db_path: Path,
    session_id: str,
    processed_terms: Optional[int] = None,
    total_products: Optional[int] = None,
    status: Optional[str] = None,
) -> None:
    updates: List[str] = []
    params: List = []

    if processed_terms is not None:
        updates.append("processed_terms = ?")
        params.append(processed_terms)

    if total_products is not None:
        updates.append("total_products = ?")
        params.append(total_products)

    if status is not None:
        updates.append("status = ?")
        params.append(status)

    if not updates:
        return

    params.append(session_id)

    with get_connection(db_path) as conn:
        conn.execute(
            f"UPDATE sessions SET {', '.join(updates)} WHERE id = ?",
            params,
        )


def store_products(
    db_path: Path,
    session_id: str,
    products: Iterable[Dict],
) -> None:
    rows = [
        (
            session_id,
            product.get("searchTerm"),
            1 if product.get("found") else 0,
            json.dumps(product),
        )
        for product in products
    ]

    if not rows:
        return

    with get_connection(db_path) as conn:
        conn.executemany(
            """
            INSERT INTO products (session_id, search_term, found, data)
            VALUES (?, ?, ?, ?)
            """,
            rows,
        )


def fetch_session(db_path: Path, session_id: str) -> Optional[Dict]:
    with get_connection(db_path) as conn:
        cur = conn.execute(
            "SELECT * FROM sessions WHERE id = ?",
            (session_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        result = dict(row)
        if result.get("region_info"):
            try:
                result["region_info"] = json.loads(result["region_info"])
            except json.JSONDecodeError:
                result["region_info"] = None
        return result


def fetch_products(db_path: Path, session_id: str) -> List[Dict]:
    with get_connection(db_path) as conn:
        cur = conn.execute(
            "SELECT data FROM products WHERE session_id = ? ORDER BY id",
            (session_id,),
        )
        products = []
        for row in cur.fetchall():
            try:
                products.append(json.loads(row["data"]))
            except json.JSONDecodeError:
                continue
        return products
