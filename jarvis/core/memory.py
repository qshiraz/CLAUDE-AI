"""Jarvis persistent memory — SQLite-backed facts, preferences, and student progress."""

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any


class MemoryManager:
    def __init__(self, db_path: Path | None = None) -> None:
        if db_path is None:
            db_path = Path(__file__).parent.parent.parent / "jarvis_memory.db"
        self._path = db_path
        self._lock = threading.Lock()
        self._init()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path)

    def _init(self) -> None:
        with self._connect() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    category TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    importance INTEGER DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(category, key)
                )""")
            c.execute("""
                CREATE TABLE IF NOT EXISTS student_progress (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    student_name TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    score INTEGER,
                    notes TEXT,
                    timestamp TEXT NOT NULL
                )""")
            c.execute("""
                CREATE TABLE IF NOT EXISTS conversation_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    timestamp TEXT NOT NULL
                )""")

    # ── Core memory ────────────────────────────────────────────────────────────

    def remember(self, category: str, key: str, value: str, importance: int = 1) -> None:
        now = datetime.now().isoformat()
        with self._lock, self._connect() as c:
            c.execute("""
                INSERT INTO memories (category, key, value, importance, created_at, updated_at)
                VALUES (?,?,?,?,?,?)
                ON CONFLICT(category,key) DO UPDATE SET
                    value=excluded.value, importance=excluded.importance, updated_at=excluded.updated_at
            """, (category, key, value, importance, now, now))

    def recall(self, category: str | None = None, limit: int = 80) -> list[dict]:
        with self._connect() as c:
            if category:
                rows = c.execute(
                    "SELECT category,key,value,importance,updated_at FROM memories WHERE category=? ORDER BY importance DESC,updated_at DESC LIMIT ?",
                    (category, limit)).fetchall()
            else:
                rows = c.execute(
                    "SELECT category,key,value,importance,updated_at FROM memories ORDER BY importance DESC,updated_at DESC LIMIT ?",
                    (limit,)).fetchall()
        return [{"category": r[0], "key": r[1], "value": r[2], "importance": r[3], "updated": r[4]} for r in rows]

    def forget(self, category: str, key: str) -> None:
        with self._lock, self._connect() as c:
            c.execute("DELETE FROM memories WHERE category=? AND key=?", (category, key))

    def all_categories(self) -> list[str]:
        with self._connect() as c:
            return [r[0] for r in c.execute("SELECT DISTINCT category FROM memories").fetchall()]

    def to_context_string(self) -> str:
        memories = self.recall(limit=100)
        if not memories:
            return ""
        grouped: dict[str, list[str]] = {}
        for m in memories:
            grouped.setdefault(m["category"], []).append(f"{m['key']}: {m['value']}")
        lines = ["[JARVIS LONG-TERM MEMORY — use this to personalise every response]"]
        for cat, items in grouped.items():
            lines.append(f"\n{cat.upper()}:")
            lines.extend(f"  • {item}" for item in items[:15])
        return "\n".join(lines)

    # ── Student progress ────────────────────────────────────────────────────────

    def log_student(self, student: str, subject: str, topic: str, score: int | None, notes: str) -> None:
        with self._lock, self._connect() as c:
            c.execute(
                "INSERT INTO student_progress (student_name,subject,topic,score,notes,timestamp) VALUES (?,?,?,?,?,?)",
                (student, subject, topic, score, notes, datetime.now().isoformat()))

    def get_student_progress(self, student: str) -> list[dict]:
        with self._connect() as c:
            rows = c.execute(
                "SELECT subject,topic,score,notes,timestamp FROM student_progress WHERE student_name=? ORDER BY timestamp DESC LIMIT 50",
                (student,)).fetchall()
        return [{"subject": r[0], "topic": r[1], "score": r[2], "notes": r[3], "timestamp": r[4][:10]} for r in rows]

    def all_students(self) -> list[str]:
        with self._connect() as c:
            return [r[0] for r in c.execute("SELECT DISTINCT student_name FROM student_progress ORDER BY student_name").fetchall()]

    def student_summary(self) -> list[dict]:
        with self._connect() as c:
            rows = c.execute("""
                SELECT student_name, COUNT(*) as sessions,
                       ROUND(AVG(CASE WHEN score IS NOT NULL THEN score END),1) as avg_score,
                       MAX(timestamp) as last_seen
                FROM student_progress GROUP BY student_name ORDER BY last_seen DESC
            """).fetchall()
        return [{"name": r[0], "sessions": r[1], "avg_score": r[2], "last_seen": (r[3] or "")[:10]} for r in rows]

    # ── Conversation log ────────────────────────────────────────────────────────

    def log_conversation(self, role: str, content: str) -> None:
        with self._lock, self._connect() as c:
            c.execute("INSERT INTO conversation_log (role,content,timestamp) VALUES (?,?,?)",
                      (role, content[:2000], datetime.now().isoformat()))
