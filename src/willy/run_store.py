"""Locked, atomic persistence primitives scoped to one ``md_run/<run_id>``."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence
import fcntl
import json
import os
import secrets
import tempfile


RUN_STORE_LOCK_FILENAME = ".run-store.lock"
EVENT_SEQUENCE_FILENAME = ".event-sequence"
PENDING_TRANSACTION_FILENAME = ".run-transaction.json"
RUN_TRANSACTION_SCHEMA_VERSION = 1


def _atomic_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _fsync_directory(directory: Path) -> None:
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


class RunStore:
    """A held transaction lock and its safe JSON/JSONL operations."""

    def __init__(self, directory: Path):
        self.directory = directory

    def read_json(self, filename: str, default: Any = None) -> Any:
        path = self.directory / filename
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return default

    def write_json(self, filename: str, payload: Any) -> None:
        _atomic_write(self.directory / filename, payload)

    def append_jsonl(self, filename: str, payload: Mapping[str, Any], *, sequenced: bool = True) -> dict[str, Any]:
        event = dict(payload)
        if sequenced:
            event["sequence"] = self._next_sequence()
        path = self.directory / filename
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return event

    def commit_bundle(
        self,
        *,
        operation: str,
        json_writes: Mapping[str, Any] | None = None,
        jsonl_appends: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    ) -> str:
        """Atomically recover-or-apply a small JSON/JSONL write bundle.

        The journal is intentionally local to one run.  A process crash after
        the intent is fsynced is repaired by the next writer: JSON writes are
        idempotent replacements and JSONL records carry a transaction ID that
        prevents duplicate append during replay.
        """
        self.recover_pending_bundle()
        transaction_id = secrets.token_urlsafe(12)
        writes = self._safe_json_writes(json_writes or {})
        appends = self._safe_jsonl_appends(jsonl_appends or {})
        journal = {
            "schema_version": RUN_TRANSACTION_SCHEMA_VERSION,
            "transaction_id": transaction_id,
            "operation": self._safe_operation(operation),
            "json_writes": writes,
            "jsonl_appends": appends,
        }
        self.write_json(PENDING_TRANSACTION_FILENAME, journal)
        self._apply_bundle(journal)
        self._clear_pending_bundle(transaction_id)
        return transaction_id

    def recover_pending_bundle(self) -> bool:
        """Replay one interrupted write bundle while the run lock is held."""
        journal = self.read_json(PENDING_TRANSACTION_FILENAME)
        if journal is None:
            return False
        if not self._valid_journal(journal):
            raise ValueError("运行事务日志格式无效")
        self._apply_bundle(journal)
        self._clear_pending_bundle(str(journal["transaction_id"]))
        return True

    def _apply_bundle(self, journal: Mapping[str, Any]) -> None:
        transaction_id = str(journal["transaction_id"])
        for filename, payload in journal["json_writes"].items():
            self.write_json(filename, payload)
        for filename, entries in journal["jsonl_appends"].items():
            if not self._jsonl_has_transaction(filename, transaction_id):
                for payload in entries:
                    self.append_jsonl(filename, {**payload, "transaction_id": transaction_id})

    def _clear_pending_bundle(self, transaction_id: str) -> None:
        path = self.directory / PENDING_TRANSACTION_FILENAME
        current = self.read_json(PENDING_TRANSACTION_FILENAME)
        if isinstance(current, Mapping) and current.get("transaction_id") == transaction_id:
            path.unlink(missing_ok=True)
            _fsync_directory(self.directory)

    def _jsonl_has_transaction(self, filename: str, transaction_id: str) -> bool:
        path = self.directory / filename
        try:
            with path.open(encoding="utf-8") as handle:
                for line in handle:
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(item, Mapping) and item.get("transaction_id") == transaction_id:
                        return True
        except OSError:
            return False
        return False

    @staticmethod
    def _safe_operation(operation: object) -> str:
        value = str(operation).strip()
        if not value or len(value) > 80 or any(marker in value for marker in ("/", "\\", "..")):
            raise ValueError("运行事务名称无效")
        return value

    @staticmethod
    def _safe_filename(filename: object) -> str:
        value = str(filename).strip()
        if (
            not value
            or Path(value).name != value
            or value == PENDING_TRANSACTION_FILENAME
            or not value.endswith((".json", ".jsonl"))
        ):
            raise ValueError("运行事务文件名无效")
        return value

    def _safe_json_writes(self, values: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(values, Mapping):
            raise ValueError("运行事务 JSON 写入格式无效")
        result: dict[str, Any] = {}
        for filename, payload in values.items():
            safe_name = self._safe_filename(filename)
            if not safe_name.endswith(".json"):
                raise ValueError("运行事务 JSON 写入必须使用 .json")
            result[safe_name] = payload
        return result

    def _safe_jsonl_appends(
        self,
        values: Mapping[str, Sequence[Mapping[str, Any]]],
    ) -> dict[str, list[dict[str, Any]]]:
        if not isinstance(values, Mapping):
            raise ValueError("运行事务 JSONL 追加格式无效")
        result: dict[str, list[dict[str, Any]]] = {}
        for filename, entries in values.items():
            safe_name = self._safe_filename(filename)
            if not safe_name.endswith(".jsonl") or not isinstance(entries, Sequence):
                raise ValueError("运行事务 JSONL 追加格式无效")
            clean_entries = []
            for entry in entries:
                if not isinstance(entry, Mapping):
                    raise ValueError("运行事务 JSONL 记录格式无效")
                clean_entries.append(dict(entry))
            result[safe_name] = clean_entries
        return result

    def _valid_journal(self, value: object) -> bool:
        if not isinstance(value, Mapping):
            return False
        if value.get("schema_version") != RUN_TRANSACTION_SCHEMA_VERSION:
            return False
        transaction_id = value.get("transaction_id")
        if not isinstance(transaction_id, str) or not transaction_id or len(transaction_id) > 128:
            return False
        try:
            self._safe_operation(value.get("operation"))
            self._safe_json_writes(value.get("json_writes", {}))
            self._safe_jsonl_appends(value.get("jsonl_appends", {}))
        except ValueError:
            return False
        return True

    def _next_sequence(self) -> int:
        path = self.directory / EVENT_SEQUENCE_FILENAME
        try:
            current = int(path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            current = 0
        sequence = current + 1
        temporary = path.with_name(f".{path.name}.tmp")
        temporary.write_text(f"{sequence}\n", encoding="utf-8")
        os.replace(temporary, path)
        _fsync_directory(self.directory)
        return sequence


@contextmanager
def run_transaction(run_dir: str | Path) -> Iterator[RunStore]:
    """Serialize run-local read-modify-write operations across all producers."""
    directory = Path(run_dir)
    directory.mkdir(parents=True, exist_ok=True)
    lock_path = directory / RUN_STORE_LOCK_FILENAME
    with lock_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield RunStore(directory)
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
