import os
import shutil
import threading
import time
from unittest.mock import patch
from pathlib import Path
import pytest
from objectdb import ObjectDB


def test_backup_of_idle_database(tmp_path):
    """A backup of an open, idle database produces a consistent snapshot."""
    db_path = tmp_path / "idle_source"
    backup_path = tmp_path / "idle_backup"

    db = ObjectDB(db_path)
    db.open()
    for i in range(25):
        db.put(i, f"payload_{i}".encode("ascii"))

    res = db.backup(backup_path)
    assert res == backup_path

    # Verify backup is complete and valid
    backup_db = ObjectDB(backup_path)
    backup_db.open()
    try:
        report = backup_db.check_integrity(raise_on_error=True)
        assert report["valid"] is True
        assert report["total_keys"] == 25
        for i in range(25):
            assert backup_db.get(i) == f"payload_{i}".encode("ascii")
    finally:
        backup_db.close()
        db.close()


def test_backup_of_closed_database(tmp_path):
    """A backup can be taken when the database instance is closed."""
    db_path = tmp_path / "closed_source"
    backup_path = tmp_path / "closed_backup"

    db = ObjectDB(db_path)
    db.open()
    for i in range(15):
        db.put(i, f"closed_val_{i}".encode("ascii"))
    db.close()

    assert db.is_open is False

    # Perform backup on closed database
    res = db.backup(backup_path)
    assert res == backup_path

    # Open backup and verify integrity
    backup_db = ObjectDB(backup_path)
    backup_db.open()
    try:
        assert backup_db.check_integrity(raise_on_error=True)["valid"] is True
        for i in range(15):
            assert backup_db.get(i) == f"closed_val_{i}".encode("ascii")
    finally:
        backup_db.close()


def test_backup_blocks_concurrent_writer_thread(tmp_path):
    """A concurrent write thread is blocked until backup creation finishes."""
    db_path = tmp_path / "thread_source"
    backup_path = tmp_path / "thread_backup"

    db = ObjectDB(db_path)
    db.open()
    for i in range(10):
        db.put(i, f"initial_{i}".encode("ascii"))

    backup_copy_started = threading.Event()
    writer_finished = threading.Event()
    thread_write_time = []
    backup_finish_time = []

    original_copy2 = shutil.copy2
    def slow_copy2(src, dst, **kwargs):
        backup_copy_started.set()
        time.sleep(0.3)  # Hold the mutation lock during copy
        return original_copy2(src, dst, **kwargs)

    def writer_worker():
        backup_copy_started.wait(timeout=5)
        # Attempt write while backup is copying
        db.put(99, b"concurrent_val")
        thread_write_time.append(time.time())
        writer_finished.set()

    writer_thread = threading.Thread(target=writer_worker)

    with patch("shutil.copy2", side_effect=slow_copy2):
        writer_thread.start()
        db.backup(backup_path)
        backup_finish_time.append(time.time())

    writer_thread.join(timeout=3)
    assert writer_finished.is_set(), "Writer thread failed to finish"

    # Verify that the writer was blocked until AFTER the backup finished copying
    assert thread_write_time[0] >= backup_finish_time[0] - 0.05

    # Verify the backup snapshot has the pre-write state (key 99 not present)
    backup_db = ObjectDB(backup_path)
    backup_db.open()
    try:
        assert backup_db.get(99) is None
        assert backup_db.check_integrity()["valid"] is True
    finally:
        backup_db.close()

    # Source db has key 99
    assert db.get(99) == b"concurrent_val"
    db.close()


def test_backup_attempted_during_active_transaction_rejected(tmp_path):
    """Attempting a backup during an active transaction is rejected per policy."""
    db_path = tmp_path / "active_tx_source"
    backup_path = tmp_path / "active_tx_backup"

    db = ObjectDB(db_path)
    db.open()

    with db.transaction():
        db.put(1, b"val1")
        with pytest.raises(RuntimeError, match="active transaction"):
            db.backup(backup_path)

    db.close()


def test_injected_error_during_file_copy_cleans_up_and_leaves_source_intact(tmp_path):
    """
    If an I/O error occurs while copying files for a backup, the incomplete
    backup is cleaned up, and the source database is undamaged.
    """
    db_path = tmp_path / "copy_err_source"
    backup_path = tmp_path / "copy_err_backup"

    db = ObjectDB(db_path)
    db.open()
    for i in range(10):
        db.put(i, f"src_val_{i}".encode("ascii"))

    original_copy2 = shutil.copy2
    def failing_copy2(src, dst, **kwargs):
        if "segment-0.dat" in str(src):
            raise OSError("Simulated disk full during segment copy")
        return original_copy2(src, dst, **kwargs)

    with patch("shutil.copy2", side_effect=failing_copy2):
        with pytest.raises(OSError, match="Simulated disk full during segment copy"):
            db.backup(backup_path)

    # Incomplete backup must not exist at backup_path
    assert not backup_path.exists(), "Incomplete backup directory should not be left on error"

    # Source database must be completely intact and functional
    assert db.check_integrity(raise_on_error=True)["valid"] is True
    for i in range(10):
        assert db.get(i) == f"src_val_{i}".encode("ascii")

    # Can continue normal mutations
    db.put(100, b"new_val")
    assert db.get(100) == b"new_val"
    db.close()


def test_restoration_from_completed_backup_validates_metadata_and_segment(tmp_path):
    """Restoration from completed backup validates segment references and independence."""
    db_path = tmp_path / "restore_source"
    backup_path = tmp_path / "restore_backup"

    db = ObjectDB(db_path)
    db.open()
    for i in range(40):
        db.put(i, f"content_{i}_{i * 10}".encode("ascii"))

    db.backup(backup_path)

    # Modify source database
    db.delete(0)
    db.put(999, b"source_only")
    db.close()

    # Open backup
    backup_db = ObjectDB(backup_path)
    backup_db.open()
    try:
        assert backup_db.check_integrity(raise_on_error=True)["valid"] is True
        assert backup_db.get(0) == f"content_0_0".encode("ascii")
        assert backup_db.get(999) is None

        # Mutate backup
        backup_db.put(5000, b"backup_mutation")
        assert backup_db.get(5000) == b"backup_mutation"
    finally:
        backup_db.close()

    # Source database is unaffected by backup mutations
    reopened_src = ObjectDB(db_path)
    reopened_src.open()
    try:
        assert reopened_src.get(0) is None
        assert reopened_src.get(999) == b"source_only"
        assert reopened_src.get(5000) is None
    finally:
        reopened_src.close()


def test_backup_replacement_failure_preserves_previous_valid_backup(tmp_path):
    """
    When replacing an existing backup, if an error occurs while moving the
    staging directory into place, the previous valid backup is restored
    and undamaged.
    """
    db_path = tmp_path / "replace_src"
    backup_path = tmp_path / "replace_dst"

    db = ObjectDB(db_path)
    db.open()
    db.put(1, b"initial_backup_val")
    db.backup(backup_path)

    # Verify initial backup is valid
    b1 = ObjectDB(backup_path)
    b1.open()
    assert b1.get(1) == b"initial_backup_val"
    b1.close()

    # Modify source db
    db.put(2, b"new_val")

    # Inject failure during the rename of staging_dir -> target_path
    real_rename = Path.rename

    def failing_rename(self, target):
        # Fail specifically when target == backup_path and self is a staging dir
        if Path(target) == backup_path and ".tmp_backup_" in str(self):
            raise OSError("Injected filesystem error during staging rename")
        return real_rename(self, target)

    with patch.object(Path, "rename", side_effect=failing_rename, autospec=True):
        with pytest.raises(OSError, match="Injected filesystem error during staging rename"):
            db.backup(backup_path)

    # Verify that the previous backup at backup_path is STILL INTACT and VALID!
    assert backup_path.exists()
    b2 = ObjectDB(backup_path)
    b2.open()
    try:
        assert b2.check_integrity()["valid"] is True
        assert b2.get(1) == b"initial_backup_val"
        assert b2.get(2) is None  # new_val was not backed up
    finally:
        b2.close()

    # Verify no temporary directories leaked
    leftover_tmp = [
        p.name for p in tmp_path.iterdir()
        if p.name.startswith(".tmp_backup_") or p.name.startswith(".old_backup_")
    ]
    assert leftover_tmp == []

    # Verify that a subsequent backup succeeds normally
    db.backup(backup_path)
    b3 = ObjectDB(backup_path)
    b3.open()
    try:
        assert b3.get(1) == b"initial_backup_val"
        assert b3.get(2) == b"new_val"
    finally:
        b3.close()

    db.close()


def test_backup_replacement_and_restoration_double_failure_preserves_old_backup(tmp_path):
    """
    When replacing an existing backup, if both the staging-to-target rename fails
    AND the old-backup-to-target restoration rename fails:
    1. The old backup is NEVER deleted and remains intact at its .old_backup_<uuid> location.
    2. Both the replacement error and restoration error are reported via exception chaining.
    3. The preserved backup files are valid and can be manually restored/opened.
    4. Successful replacements continue to work as expected.
    """
    db_path = tmp_path / "double_fail_src"
    backup_path = tmp_path / "double_fail_dst"

    db = ObjectDB(db_path)
    db.open()
    db.put(1, b"initial_valid_backup_payload")
    db.backup(backup_path)

    # Verify initial backup is valid
    b1 = ObjectDB(backup_path)
    b1.open()
    assert b1.get(1) == b"initial_valid_backup_payload"
    b1.close()

    # Modify source db
    db.put(2, b"new_mutation_payload")

    # Injected errors for both renames
    staging_err = OSError("Injected error during staging-to-target rename")
    restore_err = OSError("Injected error during old-backup-to-target restoration rename")

    real_rename = Path.rename

    def double_failing_rename(self, target):
        target_path_obj = Path(target)
        self_str = str(self)
        if target_path_obj == backup_path and ".tmp_backup_" in self_str:
            raise staging_err
        if target_path_obj == backup_path and ".old_backup_" in self_str:
            raise restore_err
        return real_rename(self, target)

    with patch.object(Path, "rename", side_effect=double_failing_rename, autospec=True):
        with pytest.raises(OSError, match="Injected error during staging-to-target rename") as exc_info:
            db.backup(backup_path)

        # Requirement 3: Both errors reported; restoration error is chained as __cause__
        assert exc_info.value is staging_err
        assert exc_info.value.__cause__ is restore_err

    # Requirement 4: Staging directory must be cleaned up
    staging_dirs = [p for p in tmp_path.iterdir() if p.name.startswith(".tmp_backup_")]
    assert staging_dirs == []

    # Requirements 1 & 2: Old backup must NOT be deleted and must be preserved in .old_backup_<uuid>
    old_backups = [p for p in tmp_path.iterdir() if p.name.startswith(".old_backup_")]
    assert len(old_backups) == 1, "The previous valid backup should be preserved in .old_backup_<uuid>"
    preserved_old_backup = old_backups[0]

    # Requirement 6: Verify old backup files remain available and valid
    assert (preserved_old_backup / "objectdb.data").exists()
    assert (preserved_old_backup / "objectdb.wal").exists()

    b_preserved = ObjectDB(preserved_old_backup)
    b_preserved.open()
    try:
        assert b_preserved.check_integrity()["valid"] is True
        assert b_preserved.get(1) == b"initial_valid_backup_payload"
        assert b_preserved.get(2) is None
    finally:
        b_preserved.close()

    # Verify manual restoration works
    preserved_old_backup.rename(backup_path)
    b_restored = ObjectDB(backup_path)
    b_restored.open()
    try:
        assert b_restored.check_integrity()["valid"] is True
        assert b_restored.get(1) == b"initial_valid_backup_payload"
        assert b_restored.get(2) is None
    finally:
        b_restored.close()

    # Requirement 7: Preserve existing successful replacement behavior
    db.backup(backup_path)
    b_replaced = ObjectDB(backup_path)
    b_replaced.open()
    try:
        assert b_replaced.check_integrity()["valid"] is True
        assert b_replaced.get(1) == b"initial_valid_backup_payload"
        assert b_replaced.get(2) == b"new_mutation_payload"
    finally:
        b_replaced.close()

    # No leftover temporary directories
    leftover = [
        p.name for p in tmp_path.iterdir()
        if p.name.startswith(".tmp_backup_") or p.name.startswith(".old_backup_")
    ]
    assert leftover == []

    db.close()


