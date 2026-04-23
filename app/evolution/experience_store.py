from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from app.models.evolution import ExperienceRecord


class SensitiveExperienceError(ValueError):
    """Raised when an experience record contains data this store must not keep."""


class ExperienceStore:
    """SQLite-backed store for lightweight Evo-Lite experience summaries."""

    _MAX_FIELD_CHARS = 1200
    _MAX_STREAM_FIELD_CHARS = 500

    _SECRET_ASSIGNMENT_RE = re.compile(
        r"(?i)\b(password|passwd|pwd|secret|private[_-]?key|api[_-]?key|"
        r"access[_-]?key|token|credential)\b\s*[:=]\s*\S+"
    )
    _PRIVATE_KEY_RE = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
    _ENV_ASSIGNMENT_RE = re.compile(r"(?m)^[A-Z_][A-Z0-9_]{1,40}=.{1,}$")
    _SHELL_PROMPT_RE = re.compile(r"(?m)^\s*(?:\$|#|>)\s+\S+")
    _RAW_COMMAND_RE = re.compile(
        r"(?m)^\s*(?:sudo\s+)?(?:rm\s+-|chmod\s+|chown\s+|useradd\s+|"
        r"userdel\s+|curl\s+https?://|wget\s+https?://|bash\s+-c|sh\s+-c)\b"
    )
    _STREAM_MARKER_RE = re.compile(r"(?i)\b(stdout|stderr|command output|traceback)\b")

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        if self.db_path.parent:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def add(self, record: ExperienceRecord) -> ExperienceRecord:
        self._reject_sensitive_record(record)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO experience_records (
                    memory_id,
                    session_id,
                    host_id,
                    intent,
                    risk_level,
                    status,
                    memory_type,
                    summary,
                    lesson,
                    tags,
                    source_request_id,
                    promoted_to_workflow,
                    created_at,
                    expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.memory_id,
                    record.session_id,
                    record.host_id,
                    record.intent,
                    record.risk_level.value,
                    record.status.value,
                    record.memory_type.value,
                    record.summary,
                    record.lesson,
                    json.dumps(record.tags, ensure_ascii=False),
                    record.source_request_id,
                    int(record.promoted_to_workflow),
                    self._serialize_datetime(record.created_at),
                    self._serialize_datetime(record.expires_at),
                ),
            )
        return record

    def get(self, memory_id: str) -> ExperienceRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM experience_records WHERE memory_id = ?",
                (memory_id,),
            ).fetchone()
        return self._row_to_record(row) if row is not None else None

    def search_by_tags(self, tags: Iterable[str], limit: int = 10) -> list[ExperienceRecord]:
        requested_tags = set(tags)
        if not requested_tags or limit <= 0:
            return []

        matches: list[ExperienceRecord] = []
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM experience_records ORDER BY created_at DESC, memory_id DESC"
            ).fetchall()

        for row in rows:
            record = self._row_to_record(row)
            if requested_tags.intersection(record.tags):
                matches.append(record)
                if len(matches) >= limit:
                    break
        return matches

    def recent(self, limit: int = 10) -> list[ExperienceRecord]:
        if limit <= 0:
            return []
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM experience_records
                ORDER BY created_at DESC, memory_id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def mark_promoted(self, memory_id: str) -> ExperienceRecord | None:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE experience_records
                SET promoted_to_workflow = 1
                WHERE memory_id = ?
                """,
                (memory_id,),
            )
            updated = cursor.rowcount > 0
        if not updated:
            return None
        return self.get(memory_id)

    def delete(self, memory_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM experience_records WHERE memory_id = ?",
                (memory_id,),
            )
            deleted = cursor.rowcount > 0
        return deleted

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS experience_records (
                    memory_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    host_id TEXT NOT NULL,
                    intent TEXT NOT NULL,
                    risk_level TEXT NOT NULL,
                    status TEXT NOT NULL,
                    memory_type TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    lesson TEXT NOT NULL,
                    tags TEXT NOT NULL,
                    source_request_id TEXT,
                    promoted_to_workflow INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    expires_at TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_experience_records_created_at
                ON experience_records (created_at)
                """
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @classmethod
    def _row_to_record(cls, row: sqlite3.Row) -> ExperienceRecord:
        return ExperienceRecord(
            memory_id=row["memory_id"],
            session_id=row["session_id"],
            host_id=row["host_id"],
            intent=row["intent"],
            risk_level=row["risk_level"],
            status=row["status"],
            memory_type=row["memory_type"],
            summary=row["summary"],
            lesson=row["lesson"],
            tags=json.loads(row["tags"]),
            source_request_id=row["source_request_id"],
            promoted_to_workflow=bool(row["promoted_to_workflow"]),
            created_at=cls._parse_datetime(row["created_at"]),
            expires_at=cls._parse_datetime(row["expires_at"]),
        )

    @staticmethod
    def _serialize_datetime(value: datetime | None) -> str | None:
        return value.isoformat() if value is not None else None

    @staticmethod
    def _parse_datetime(value: str | None) -> datetime | None:
        return datetime.fromisoformat(value) if value else None

    @classmethod
    def _reject_sensitive_record(cls, record: ExperienceRecord) -> None:
        for text in [record.summary, record.lesson, *record.tags]:
            cls._reject_sensitive_text(text)

    @classmethod
    def _reject_sensitive_text(cls, text: str) -> None:
        if len(text) > cls._MAX_FIELD_CHARS:
            raise SensitiveExperienceError("experience field is too large")
        if cls._STREAM_MARKER_RE.search(text) and len(text) > cls._MAX_STREAM_FIELD_CHARS:
            raise SensitiveExperienceError("experience field looks like raw command output")
        if cls._SECRET_ASSIGNMENT_RE.search(text):
            raise SensitiveExperienceError("experience field contains a secret-like assignment")
        if cls._PRIVATE_KEY_RE.search(text):
            raise SensitiveExperienceError("experience field contains private key material")
        if len(cls._ENV_ASSIGNMENT_RE.findall(text)) >= 2:
            raise SensitiveExperienceError("experience field looks like a full environment dump")
        if cls._SHELL_PROMPT_RE.search(text) or cls._RAW_COMMAND_RE.search(text):
            raise SensitiveExperienceError("experience field looks like raw user command text")
