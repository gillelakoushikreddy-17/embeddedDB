from unittest.mock import patch
import pytest
from embeddeddb import EmbeddedDB
from embeddeddb.database import _ACTIVE_INSTANCES
from embeddeddb.page_manager import PageManager
from embeddeddb.wal import WriteAheadLog
from embeddeddb.segment import Segment
from embeddeddb.recovery import RecoveryManager


def test_failure_in_page_manager_initialize_cleans_up_lock_and_registry(tmp_path):
    """If PageManager.initialize fails, lock and registry are released."""
    db_path = tmp_path / "init_fail_db"
    db = EmbeddedDB(db_path)

    canonical_path = str(db_path.resolve())

    with patch.object(PageManager, "initialize", side_effect=OSError("Disk full on init")):
        with pytest.raises(OSError, match="Disk full on init"):
            db.open()

    # Verify no stale registration in _ACTIVE_INSTANCES
    assert canonical_path not in _ACTIVE_INSTANCES
    assert db._lock_fp is None
    assert db.is_open is False

    # A subsequent open attempt succeeds
    db2 = EmbeddedDB(db_path)
    db2.open()
    assert db2.is_open is True
    db2.close()


def test_failure_in_page_manager_open_cleans_up_lock_and_registry(tmp_path):
    """If PageManager.open fails, lock and registry are released."""
    db_path = tmp_path / "pm_open_fail_db"
    db = EmbeddedDB(db_path)
    canonical_path = str(db_path.resolve())

    with patch.object(PageManager, "open", side_effect=ValueError("Corrupt header")):
        with pytest.raises(ValueError, match="Corrupt header"):
            db.open()

    assert canonical_path not in _ACTIVE_INSTANCES
    assert db._lock_fp is None
    assert db.is_open is False

    # A new instance can open after resolution
    db2 = EmbeddedDB(db_path)
    db2.open()
    db2.close()


def test_failure_in_wal_open_cleans_up_manager_and_lock(tmp_path):
    """If WriteAheadLog.open fails, manager and lock are closed and registry cleared."""
    db_path = tmp_path / "wal_open_fail_db"
    db = EmbeddedDB(db_path)
    canonical_path = str(db_path.resolve())

    with patch.object(WriteAheadLog, "open", side_effect=ValueError("Corrupt WAL")):
        with pytest.raises(ValueError, match="Corrupt WAL"):
            db.open()

    assert canonical_path not in _ACTIVE_INSTANCES
    assert db._lock_fp is None
    assert db.is_open is False

    # A new instance can open after resolution
    db2 = EmbeddedDB(db_path)
    db2.open()
    db2.close()


def test_failure_in_recovery_cleans_up_all_resources(tmp_path):
    """If RecoveryManager.recover fails, manager, wal, and lock are closed."""
    db_path = tmp_path / "recovery_fail_db"
    db = EmbeddedDB(db_path)
    canonical_path = str(db_path.resolve())

    with patch.object(RecoveryManager, "recover", side_effect=RuntimeError("Recovery crash")):
        with pytest.raises(RuntimeError, match="Recovery crash"):
            db.open()

    assert canonical_path not in _ACTIVE_INSTANCES
    assert db._lock_fp is None
    assert db.is_open is False

    db2 = EmbeddedDB(db_path)
    db2.open()
    db2.close()


def test_failure_in_segment_open_cleans_up_all_resources(tmp_path):
    """If Segment.open fails, manager, wal, and lock are closed."""
    db_path = tmp_path / "seg_fail_db"
    db = EmbeddedDB(db_path)
    canonical_path = str(db_path.resolve())

    with patch.object(Segment, "open", side_effect=OSError("Segment file access error")):
        with pytest.raises(OSError, match="Segment file access error"):
            db.open()

    assert canonical_path not in _ACTIVE_INSTANCES
    assert db._lock_fp is None
    assert db.is_open is False

    db2 = EmbeddedDB(db_path)
    db2.open()
    db2.close()


def test_partially_initialized_instance_cannot_be_used(tmp_path):
    """Operations on an instance whose open() failed are rejected with RuntimeError."""
    db_path = tmp_path / "partial_db"
    db = EmbeddedDB(db_path)

    with patch.object(Segment, "open", side_effect=OSError("Failed open")):
        with pytest.raises(OSError):
            db.open()

    assert db.is_open is False
    with pytest.raises(RuntimeError, match="Database is not open"):
        db.put(1, b"val")

    with pytest.raises(RuntimeError, match="Database is not open"):
        db.get(1)

    with pytest.raises(RuntimeError, match="Database is not open"):
        db.delete(1)

    with pytest.raises(RuntimeError, match="Database is not open"):
        db.check_integrity()


def test_cleanup_exception_does_not_mask_original_exception(tmp_path):
    """If a cleanup step (e.g. manager.close) raises during error handling, original error is preserved."""
    db_path = tmp_path / "mask_test_db"
    db = EmbeddedDB(db_path)

    with patch.object(PageManager, "close", side_effect=OSError("Cleanup close error")):
        with patch.object(WriteAheadLog, "open", side_effect=ValueError("Original initialization error")):
            with pytest.raises(ValueError, match="Original initialization error"):
                db.open()

    assert db.is_open is False


def test_repeated_close_is_idempotent(tmp_path):
    """Repeated calls to close() behave consistently and safely."""
    db_path = tmp_path / "idempotent_close_db"
    db = EmbeddedDB(db_path)
    db.open()
    db.put(1, b"one")
    db.close()

    # Subsequent close calls do not raise exceptions
    db.close()
    db.close()
    assert db.is_open is False
