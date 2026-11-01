from unittest.mock import patch
import pytest
from embeddeddb import EmbeddedDB
from embeddeddb.wal import WriteAheadLog
from embeddeddb.transaction import TransactionState


def test_wal_append_write_failure_poisons_database_and_recovers_on_reopen(tmp_path):
    """
    When wal.append fails during write, the WAL marks itself broken,
    the database is poisoned, and reopening restores consistent state.
    """
    db_path = tmp_path / "wal_write_fail_db"
    db = EmbeddedDB(db_path)
    db.open()
    db.put(1, b"val1")

    # Injected write failure
    original_write = db.wal.file.write
    def failing_write(data):
        raise OSError("Disk full on write")

    db.wal.file.write = failing_write

    with pytest.raises(OSError, match="Disk full on write"):
        db.put(2, b"val2")

    # WAL is broken
    assert db.wal._is_broken is True
    with pytest.raises(RuntimeError, match="broken/uncertain state"):
        db.wal.append("TEST", {})

    # Database is poisoned
    assert db.is_poisoned is True
    with pytest.raises(RuntimeError, match="poisoned state"):
        db.put(3, b"val3")

    with pytest.raises(RuntimeError, match="poisoned state"):
        db.get(1)

    db.close()

    # Reopening resets broken/poisoned state and recovers
    reopened = EmbeddedDB(db_path)
    reopened.open()
    try:
        assert reopened.get(1) == b"val1"
        assert reopened.get(2) is None
        report = reopened.check_integrity(raise_on_error=True)
        assert report["valid"] is True

        # Can now accept writes normally
        reopened.put(4, b"val4")
        assert reopened.get(4) == b"val4"
    finally:
        reopened.close()


def test_wal_append_flush_failure_poisons_database(tmp_path):
    """When wal.append fails during flush, the database is poisoned."""
    db_path = tmp_path / "wal_flush_fail_db"
    db = EmbeddedDB(db_path)
    db.open()
    db.put(1, b"val1")

    with patch.object(db.wal.file, "flush", side_effect=OSError("Flush I/O error")):
        with pytest.raises(OSError, match="Flush I/O error"):
            db.put(2, b"val2")

    assert db.wal._is_broken is True
    assert db.is_poisoned is True

    db.close()

    reopened = EmbeddedDB(db_path)
    reopened.open()
    try:
        assert reopened.get(1) == b"val1"
        assert reopened.get(2) is None
        assert reopened.check_integrity()["valid"] is True
    finally:
        reopened.close()


def test_wal_append_fsync_failure_with_complete_commit_on_disk(tmp_path):
    """
    If fsync fails after a COMMIT record was written to the file,
    the in-memory database fails closed (poisoned), but reopening recovers
    and recognizes the committed record because it was completely written.
    """
    db_path = tmp_path / "wal_fsync_fail_db"
    db = EmbeddedDB(db_path)
    db.open()
    db.put(1, b"val1")

    import os
    original_fsync = os.fsync
    def failing_fsync(fd):
        if fd == db.wal.file.fileno() and getattr(db.wal, "_current_record_type", None) == "COMMIT":
            # COMMIT data was written to buffer and flushed, but fsync reports error
            raise OSError("I/O error on fsync")
        return original_fsync(fd)

    with patch("os.fsync", side_effect=failing_fsync):
        with pytest.raises(OSError, match="I/O error on fsync"):
            db.put(2, b"val2")

    # In-memory database correctly fails closed
    assert db.is_poisoned is True

    db.close()

    # Reopening parses the valid durable COMMIT record and preserves it
    reopened = EmbeddedDB(db_path)
    reopened.open()
    try:
        assert reopened.get(1) == b"val1"
        assert reopened.get(2) == b"val2"
        assert reopened.check_integrity()["valid"] is True
    finally:
        reopened.close()


def test_partial_wal_write_detected_and_truncated_on_reopen(tmp_path):
    """An unterminated partial record at the end of WAL is discarded on reopen."""
    db_path = tmp_path / "partial_wal_db"
    db = EmbeddedDB(db_path)
    db.open()
    db.put(1, b"val1")
    db.close()

    # Append an incomplete, unterminated line to simulate sudden crash during write
    wal_file = db_path / "embeddeddb.wal"
    with open(wal_file, "ab") as f:
        f.write(b'{"lsn": 10, "type": "PAGE_BEFORE_IMAGE", "data": {"tra')

    reopened = EmbeddedDB(db_path)
    reopened.open()
    try:
        assert reopened.get(1) == b"val1"
        report = reopened.check_integrity(raise_on_error=True)
        assert report["valid"] is True

        # WAL continues with clean sequential LSN
        reopened.put(2, b"val2")
        assert reopened.get(2) == b"val2"
    finally:
        reopened.close()


def test_cleanup_failure_during_wal_append_chains_exception(tmp_path):
    """
    If cleanup (truncate) fails after write fails, both exceptions are chained
    and neither is silently swallowed.
    """
    wal_file = tmp_path / "test_chain.wal"
    wal = WriteAheadLog(wal_file)
    wal.open()

    write_err = OSError("Disk write failed")
    trunc_err = OSError("Truncate cleanup failed")

    with patch.object(wal.file, "write", side_effect=write_err):
        with patch.object(wal.file, "truncate", side_effect=trunc_err):
            with pytest.raises(OSError, match="Disk write failed") as exc_info:
                wal.append("TEST", {"key": "val"})

            # Verify exception chaining
            assert exc_info.value is write_err
            assert exc_info.value.__cause__ is trunc_err

    assert wal._is_broken is True
    wal.close()


def test_commit_failure_prevents_unsafe_rollback_and_poisons_db(tmp_path):
    """A commit that fails after page sync cannot be rolled back and poisons the DB."""
    db_path = tmp_path / "commit_fail_db"
    db = EmbeddedDB(db_path)
    db.open()
    db.put(1, b"val1")

    original_append = db.wal.append
    def failing_append(record_type, data):
        if record_type == "COMMIT":
            raise OSError("I/O error appending COMMIT")
        return original_append(record_type, data)

    db.wal.append = failing_append

    with pytest.raises(OSError, match="I/O error appending COMMIT"):
        db.put(2, b"val2")

    assert db.is_poisoned is True

    # Database refuses any more operations
    with pytest.raises(RuntimeError, match="poisoned state"):
        db.get(1)

    db.close()

    # Reopening recovers to consistent state
    reopened = EmbeddedDB(db_path)
    reopened.open()
    try:
        assert reopened.get(1) == b"val1"
        assert reopened.get(2) is None
        assert reopened.check_integrity()["valid"] is True
    finally:
        reopened.close()
