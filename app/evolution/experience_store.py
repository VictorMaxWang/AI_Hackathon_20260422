from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.models.evolution import (
    ExperienceRecord,
    GovernanceStatus,
    MemoryType,
    build_experience_dedup_hash,
)
from app.models.policy import RiskLevel
from app.models.result import ExecutionStatus


class SensitiveExperienceError(ValueError):
    """Raised when an experience record contains data this store must not keep."""


class GovernanceTransitionError(ValueError):
    """Raised when a governance transition violates deterministic guardrails."""


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
    _PROMOTION_RISK_ALLOWLIST = {RiskLevel.S0, RiskLevel.S1}

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        if self.db_path.parent:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def add(self, record: ExperienceRecord) -> ExperienceRecord:
        candidate = self._prepare_new_record(record)
        self._reject_sensitive_record(candidate)

        with self._connect() as connection:
            existing_row = None
            if candidate.evidence_refs:
                existing_row = connection.execute(
                    """
                    SELECT * FROM experience_records
                    WHERE dedup_hash = ?
                    ORDER BY created_at DESC, memory_id DESC
                    LIMIT 1
                    """,
                    (candidate.dedup_hash,),
                ).fetchone()

            if existing_row is not None:
                merged = self._merge_duplicate_records(self._row_to_record(existing_row), candidate)
                self._update_record(connection, merged)
                return merged

            self._insert_record(connection, candidate)
        return candidate

    def get(self, memory_id: str) -> ExperienceRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM experience_records WHERE memory_id = ?",
                (memory_id,),
            ).fetchone()
        return self._row_to_record(row) if row is not None else None

    def search_by_tags(
        self,
        tags: Iterable[str],
        limit: int = 10,
        *,
        include_tombstoned: bool = False,
    ) -> list[ExperienceRecord]:
        requested_tags = {tag.strip() for tag in tags if isinstance(tag, str) and tag.strip()}
        if not requested_tags or limit <= 0:
            return []

        matches: list[ExperienceRecord] = []
        query = "SELECT * FROM experience_records"
        params: tuple[Any, ...] = ()
        if not include_tombstoned:
            query += " WHERE governance_status != ?"
            params = (GovernanceStatus.TOMBSTONED.value,)
        query += " ORDER BY created_at DESC, memory_id DESC"

        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()

        for row in rows:
            record = self._row_to_record(row)
            if requested_tags.intersection(record.tags):
                matches.append(record)
                if len(matches) >= limit:
                    break
        return matches

    def recent(
        self,
        limit: int = 10,
        *,
        include_tombstoned: bool = False,
    ) -> list[ExperienceRecord]:
        if limit <= 0:
            return []

        query = "SELECT * FROM experience_records"
        params: tuple[Any, ...] = ()
        if not include_tombstoned:
            query += " WHERE governance_status != ?"
            params = (GovernanceStatus.TOMBSTONED.value,)
        query += " ORDER BY created_at DESC, memory_id DESC LIMIT ?"
        params = (*params, limit)

        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._row_to_record(row) for row in rows]

    def verify(
        self,
        memory_id: str,
        *,
        evidence_refs: Iterable[str] | None = None,
    ) -> ExperienceRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM experience_records WHERE memory_id = ?",
                (memory_id,),
            ).fetchone()
            if row is None:
                return None

            record = self._row_to_record(row)
            if record.governance_status == GovernanceStatus.TOMBSTONED:
                raise GovernanceTransitionError("tombstoned experiences cannot be verified")

            merged_refs = self._merge_string_lists(record.evidence_refs, list(evidence_refs or []))
            if not merged_refs:
                raise GovernanceTransitionError("verified experience requires evidence_refs")

            updated = record.model_copy(
                update={
                    "evidence_refs": merged_refs,
                    "governance_status": (
                        record.governance_status
                        if record.governance_status == GovernanceStatus.PROMOTED
                        else GovernanceStatus.VERIFIED
                    ),
                    "provenance": self._merge_provenance(
                        record.provenance,
                        {"sources": ["manual_verification"]},
                    ),
                }
            )
            updated = self._recompute_governance(updated)
            self._update_record(connection, updated)
            return updated

    def mark_promoted(self, memory_id: str) -> ExperienceRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM experience_records WHERE memory_id = ?",
                (memory_id,),
            ).fetchone()
            if row is None:
                return None

            record = self._row_to_record(row)
            if record.governance_status == GovernanceStatus.TOMBSTONED:
                raise GovernanceTransitionError("tombstoned experiences cannot be promoted")

            promoted = record.model_copy(
                update={
                    "governance_status": GovernanceStatus.PROMOTED,
                    "promoted_to_workflow": True,
                    "provenance": self._merge_provenance(
                        record.provenance,
                        {"sources": ["manual_promotion"]},
                    ),
                }
            )
            promoted = self._recompute_governance(promoted)
            self._update_record(connection, promoted)
            return promoted

    def apply_decay(
        self,
        *,
        now: datetime | None = None,
        stale_after_days: int = 30,
        increment: float = 0.25,
    ) -> list[ExperienceRecord]:
        if stale_after_days < 0:
            raise ValueError("stale_after_days must be non-negative")
        if increment <= 0:
            raise ValueError("increment must be greater than zero")

        current_time = now or datetime.now(timezone.utc)
        updated_records: list[ExperienceRecord] = []

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM experience_records
                WHERE governance_status != ?
                ORDER BY created_at ASC, memory_id ASC
                """,
                (GovernanceStatus.TOMBSTONED.value,),
            ).fetchall()

            for row in rows:
                record = self._row_to_record(row)
                age_days = (current_time - record.created_at).days
                if age_days < stale_after_days:
                    continue

                decayed = record.model_copy(
                    update={
                        "decay_score": min(1.0, record.decay_score + increment),
                        "provenance": self._merge_provenance(
                            record.provenance,
                            {"sources": ["decay"]},
                        ),
                    }
                )
                decayed = self._recompute_governance(decayed)
                self._update_record(connection, decayed)
                updated_records.append(decayed)

        return updated_records

    def tombstone(self, memory_id: str, *, reason: str | None = None) -> ExperienceRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM experience_records WHERE memory_id = ?",
                (memory_id,),
            ).fetchone()
            if row is None:
                return None

            record = self._row_to_record(row)
            tombstoned = record.model_copy(
                update={
                    "governance_status": GovernanceStatus.TOMBSTONED,
                    "promotion_gate_passed": False,
                    "promoted_to_workflow": False,
                    "provenance": self._merge_provenance(
                        record.provenance,
                        {
                            "sources": ["manual_tombstone"],
                            "tombstone_reasons": [reason] if reason else [],
                        },
                    ),
                }
            )
            tombstoned = self._recompute_governance(tombstoned)
            self._update_record(connection, tombstoned)
            return tombstoned

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
                    provenance TEXT NOT NULL DEFAULT '{}',
                    evidence_refs TEXT NOT NULL DEFAULT '[]',
                    dedup_hash TEXT NOT NULL DEFAULT '',
                    governance_status TEXT NOT NULL DEFAULT 'quarantine',
                    decay_score REAL NOT NULL DEFAULT 0,
                    promotion_gate_passed INTEGER NOT NULL DEFAULT 0,
                    host_scope TEXT NOT NULL DEFAULT '[]',
                    session_scope TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    expires_at TEXT
                )
                """
            )

            existing_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(experience_records)").fetchall()
            }
            for column_name, column_sql in self._required_columns().items():
                if column_name not in existing_columns:
                    connection.execute(
                        f"ALTER TABLE experience_records ADD COLUMN {column_name} {column_sql}"
                    )

            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_experience_records_created_at
                ON experience_records (created_at)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_experience_records_dedup_hash
                ON experience_records (dedup_hash)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_experience_records_governance_status
                ON experience_records (governance_status)
                """
            )

            rows = connection.execute("SELECT * FROM experience_records").fetchall()
            for row in rows:
                normalized = self._prepare_loaded_record(self._row_to_record(row))
                self._update_record(connection, normalized)

    @staticmethod
    def _required_columns() -> dict[str, str]:
        return {
            "provenance": "TEXT NOT NULL DEFAULT '{}'",
            "evidence_refs": "TEXT NOT NULL DEFAULT '[]'",
            "dedup_hash": "TEXT NOT NULL DEFAULT ''",
            "governance_status": "TEXT NOT NULL DEFAULT 'quarantine'",
            "decay_score": "REAL NOT NULL DEFAULT 0",
            "promotion_gate_passed": "INTEGER NOT NULL DEFAULT 0",
            "host_scope": "TEXT NOT NULL DEFAULT '[]'",
            "session_scope": "TEXT NOT NULL DEFAULT '[]'",
        }

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

    def _insert_record(self, connection: sqlite3.Connection, record: ExperienceRecord) -> None:
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
                provenance,
                evidence_refs,
                dedup_hash,
                governance_status,
                decay_score,
                promotion_gate_passed,
                host_scope,
                session_scope,
                created_at,
                expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            self._record_params(record),
        )

    def _update_record(self, connection: sqlite3.Connection, record: ExperienceRecord) -> None:
        connection.execute(
            """
            UPDATE experience_records
            SET
                session_id = ?,
                host_id = ?,
                intent = ?,
                risk_level = ?,
                status = ?,
                memory_type = ?,
                summary = ?,
                lesson = ?,
                tags = ?,
                source_request_id = ?,
                promoted_to_workflow = ?,
                provenance = ?,
                evidence_refs = ?,
                dedup_hash = ?,
                governance_status = ?,
                decay_score = ?,
                promotion_gate_passed = ?,
                host_scope = ?,
                session_scope = ?,
                created_at = ?,
                expires_at = ?
            WHERE memory_id = ?
            """,
            (*self._record_params(record)[1:], record.memory_id),
        )

    def _record_params(self, record: ExperienceRecord) -> tuple[Any, ...]:
        return (
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
            json.dumps(record.provenance, ensure_ascii=False),
            json.dumps(record.evidence_refs, ensure_ascii=False),
            record.dedup_hash,
            record.governance_status.value,
            float(record.decay_score),
            int(record.promotion_gate_passed),
            json.dumps(record.host_scope, ensure_ascii=False),
            json.dumps(record.session_scope, ensure_ascii=False),
            self._serialize_datetime(record.created_at),
            self._serialize_datetime(record.expires_at),
        )

    def _prepare_new_record(self, record: ExperienceRecord) -> ExperienceRecord:
        prepared = record.model_copy(
            update={
                "dedup_hash": record.dedup_hash
                or build_experience_dedup_hash(record.intent, record.summary, record.lesson),
                "governance_status": GovernanceStatus.QUARANTINE,
                "promotion_gate_passed": False,
                "promoted_to_workflow": False,
                "host_scope": self._merge_string_lists(record.host_scope, [record.host_id]),
                "session_scope": self._merge_string_lists(record.session_scope, [record.session_id]),
            }
        )
        return self._recompute_governance(prepared)

    def _prepare_loaded_record(self, record: ExperienceRecord) -> ExperienceRecord:
        loaded = record.model_copy(
            update={
                "dedup_hash": record.dedup_hash
                or build_experience_dedup_hash(record.intent, record.summary, record.lesson),
                "host_scope": self._merge_string_lists(record.host_scope, [record.host_id]),
                "session_scope": self._merge_string_lists(record.session_scope, [record.session_id]),
                "promoted_to_workflow": (
                    record.promoted_to_workflow
                    if record.governance_status == GovernanceStatus.PROMOTED
                    else False
                ),
            }
        )
        return self._recompute_governance(loaded)

    def _merge_duplicate_records(
        self,
        existing: ExperienceRecord,
        incoming: ExperienceRecord,
    ) -> ExperienceRecord:
        governance_status = existing.governance_status
        merged = existing.model_copy(
            update={
                "session_id": existing.session_id,
                "host_id": existing.host_id,
                "risk_level": self._merge_risk_level(existing.risk_level, incoming.risk_level),
                "status": self._merge_execution_status(existing.status, incoming.status),
                "memory_type": self._merge_memory_type(existing.memory_type, incoming.memory_type),
                "tags": self._merge_string_lists(existing.tags, incoming.tags),
                "source_request_id": incoming.source_request_id or existing.source_request_id,
                "promoted_to_workflow": (
                    existing.promoted_to_workflow if governance_status == GovernanceStatus.PROMOTED else False
                ),
                "provenance": self._merge_provenance(existing.provenance, incoming.provenance),
                "evidence_refs": self._merge_string_lists(existing.evidence_refs, incoming.evidence_refs),
                "dedup_hash": existing.dedup_hash or incoming.dedup_hash,
                "governance_status": governance_status,
                "decay_score": max(existing.decay_score, incoming.decay_score),
                "host_scope": self._merge_string_lists(existing.host_scope, incoming.host_scope),
                "session_scope": self._merge_string_lists(existing.session_scope, incoming.session_scope),
                "created_at": min(existing.created_at, incoming.created_at),
                "expires_at": existing.expires_at or incoming.expires_at,
            }
        )
        return self._recompute_governance(merged)

    def _recompute_governance(self, record: ExperienceRecord) -> ExperienceRecord:
        governance_status = record.governance_status
        promoted_to_workflow = governance_status == GovernanceStatus.PROMOTED
        promotion_gate_passed = self._passes_promotion_gate(record)

        if governance_status == GovernanceStatus.TOMBSTONED:
            promotion_gate_passed = False
            promoted_to_workflow = False

        return record.model_copy(
            update={
                "governance_status": governance_status,
                "promotion_gate_passed": promotion_gate_passed,
                "promoted_to_workflow": promoted_to_workflow,
            }
        )

    def _passes_promotion_gate(self, record: ExperienceRecord) -> bool:
        if record.governance_status not in {
            GovernanceStatus.VERIFIED,
            GovernanceStatus.PROMOTED,
        }:
            return False
        if record.governance_status == GovernanceStatus.TOMBSTONED:
            return False
        if record.memory_type != MemoryType.PROCEDURAL:
            return False
        if record.status != ExecutionStatus.SUCCESS:
            return False
        if record.risk_level not in self._PROMOTION_RISK_ALLOWLIST:
            return False
        if record.decay_score >= 1.0:
            return False
        if len(record.evidence_refs) < 2:
            return False
        if len(self._provenance_request_ids(record.provenance)) < 2:
            return False
        if "high_risk_refusal" in {tag.strip() for tag in record.tags}:
            return False
        return True

    def _provenance_request_ids(self, provenance: dict[str, Any]) -> list[str]:
        request_ids = provenance.get("request_ids")
        if not isinstance(request_ids, list):
            return []
        return self._merge_string_lists(request_ids, [])

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
            tags=cls._deserialize_json_list(row["tags"]),
            source_request_id=row["source_request_id"],
            promoted_to_workflow=bool(row["promoted_to_workflow"]),
            provenance=cls._deserialize_json_dict(row["provenance"]),
            evidence_refs=cls._deserialize_json_list(row["evidence_refs"]),
            dedup_hash=str(row["dedup_hash"] or "").strip(),
            governance_status=row["governance_status"] or GovernanceStatus.QUARANTINE.value,
            decay_score=float(row["decay_score"] or 0.0),
            promotion_gate_passed=bool(row["promotion_gate_passed"]),
            host_scope=cls._deserialize_json_list(row["host_scope"]),
            session_scope=cls._deserialize_json_list(row["session_scope"]),
            created_at=cls._parse_datetime(row["created_at"]),
            expires_at=cls._parse_datetime(row["expires_at"]),
        )

    @staticmethod
    def _serialize_datetime(value: datetime | None) -> str | None:
        return value.isoformat() if value is not None else None

    @staticmethod
    def _parse_datetime(value: str | None) -> datetime | None:
        return datetime.fromisoformat(value) if value else None

    @staticmethod
    def _deserialize_json_list(value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, list):
            raw = value
        else:
            try:
                raw = json.loads(value)
            except (TypeError, json.JSONDecodeError):
                raw = []
        if not isinstance(raw, list):
            return []
        cleaned = [str(item).strip() for item in raw if str(item).strip()]
        return list(dict.fromkeys(cleaned))

    @staticmethod
    def _deserialize_json_dict(value: Any) -> dict[str, Any]:
        if value is None:
            return {}
        if isinstance(value, dict):
            return value
        try:
            loaded = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return {}
        return loaded if isinstance(loaded, dict) else {}

    @classmethod
    def _reject_sensitive_record(cls, record: ExperienceRecord) -> None:
        texts = [
            record.summary,
            record.lesson,
            *record.tags,
            *record.evidence_refs,
            cls._flatten_text(record.provenance),
        ]
        for text in texts:
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

    @classmethod
    def _flatten_text(cls, value: Any) -> str:
        if isinstance(value, dict):
            return " ".join(cls._flatten_text(item) for item in value.values())
        if isinstance(value, list):
            return " ".join(cls._flatten_text(item) for item in value)
        if value is None:
            return ""
        return str(value)

    @staticmethod
    def _merge_string_lists(*values: Iterable[str]) -> list[str]:
        merged: list[str] = []
        for value in values:
            for item in value:
                cleaned = str(item).strip()
                if cleaned and cleaned not in merged:
                    merged.append(cleaned)
        return merged

    @classmethod
    def _merge_provenance(cls, left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
        merged: dict[str, Any] = dict(left)
        for key, right_value in right.items():
            if key not in merged:
                merged[key] = right_value
                continue

            left_value = merged[key]
            if isinstance(left_value, dict) and isinstance(right_value, dict):
                merged[key] = cls._merge_provenance(left_value, right_value)
                continue
            if isinstance(left_value, list) or isinstance(right_value, list):
                left_list = left_value if isinstance(left_value, list) else [left_value]
                right_list = right_value if isinstance(right_value, list) else [right_value]
                merged[key] = cls._merge_string_lists(left_list, right_list)
                continue
            if left_value == right_value:
                merged[key] = left_value
                continue
            merged[key] = cls._merge_string_lists([left_value], [right_value])
        return merged

    @staticmethod
    def _merge_risk_level(left: RiskLevel, right: RiskLevel) -> RiskLevel:
        order = {
            RiskLevel.S0: 0,
            RiskLevel.S1: 1,
            RiskLevel.S2: 2,
            RiskLevel.S3: 3,
        }
        return left if order[left] >= order[right] else right

    @staticmethod
    def _merge_execution_status(left: ExecutionStatus, right: ExecutionStatus) -> ExecutionStatus:
        order = {
            ExecutionStatus.SUCCESS: 0,
            ExecutionStatus.PENDING_CONFIRMATION: 1,
            ExecutionStatus.FAILED: 2,
            ExecutionStatus.REFUSED: 3,
        }
        return left if order[left] >= order[right] else right

    @staticmethod
    def _merge_memory_type(left: MemoryType, right: MemoryType) -> MemoryType:
        order = {
            MemoryType.NONE: 0,
            MemoryType.SEMANTIC: 1,
            MemoryType.PROCEDURAL: 2,
            MemoryType.EPISODIC: 3,
        }
        return left if order[left] >= order[right] else right
