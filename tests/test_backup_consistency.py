import errno
import os
import shutil
import threading
import time
from unittest.mock import patch
from pathlib import Path
import pytest
from objectdb import ObjectDB
from objectdb import database


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


def test_backup_staging_dir_fsync_genuine_io_failure_propagates_and_cleans_up(tmp_path):
    """
    If a genuine filesystem I/O error occurs while syncing the staging directory metadata,
    the error must propagate, the backup is not claimed to be complete,
    and staging files are cleaned up without damaging the source database.
    """
    db_path = tmp_path / "sync_fail_src"
    backup_path = tmp_path / "sync_fail_dst"

    db = ObjectDB(db_path)
    db.open()
    db.put(1, b"val1")

    # Injected genuine I/O error when fsyncing staging directory
    io_err = OSError(errno.EIO, "Simulated disk hardware I/O error on staging dir")

    original_sync = database._sync_directory

    def failing_sync(path):
        if ".tmp_backup_" in str(path):
            raise io_err
        return original_sync(path)

    with patch("objectdb.database._sync_directory", side_effect=failing_sync):
        with pytest.raises(OSError, match="Simulated disk hardware I/O error") as exc_info:
            db.backup(backup_path)
        assert exc_info.value is io_err

    # Target path was never created
    assert not backup_path.exists()

    # Staging directory must be cleaned up
    staging_dirs = [p for p in tmp_path.iterdir() if p.name.startswith(".tmp_backup_")]
    assert staging_dirs == []

    # Source database must remain intact
    assert db.get(1) == b"val1"
    assert db.check_integrity()["valid"] is True
    db.close()


def test_backup_parent_dir_fsync_genuine_io_failure_restores_previous_valid_backup(tmp_path):
    """
    When replacing an existing backup, if a genuine I/O failure occurs while syncing
    the target parent directory, the failure propagates, the replacement is aborted,
    and the previous valid backup is restored to target_path.
    """
    db_path = tmp_path / "parent_sync_src"
    backup_path = tmp_path / "parent_sync_dst"

    db = ObjectDB(db_path)
    db.open()
    db.put(1, b"original_backup_payload")
    db.backup(backup_path)

    # Verify initial backup
    b1 = ObjectDB(backup_path)
    b1.open()
    assert b1.get(1) == b"original_backup_payload"
    b1.close()

    # Mutate source database
    db.put(2, b"new_mutation_payload")

    io_err = OSError(errno.EIO, "Disk I/O error syncing parent directory")

    original_sync = database._sync_directory

    def failing_parent_sync(path):
        if Path(path) == backup_path.parent:
            raise io_err
        return original_sync(path)

    with patch("objectdb.database._sync_directory", side_effect=failing_parent_sync):
        with pytest.raises(OSError, match="Disk I/O error syncing parent directory") as exc_info:
            db.backup(backup_path)
        assert exc_info.value is io_err

    # Verify previous backup is restored at backup_path and completely intact
    assert backup_path.exists()
    b_restored = ObjectDB(backup_path)
    b_restored.open()
    try:
        assert b_restored.check_integrity()["valid"] is True
        assert b_restored.get(1) == b"original_backup_payload"
        assert b_restored.get(2) is None  # new_mutation_payload was rejected due to sync failure
    finally:
        b_restored.close()

    # Verify no temporary directories leaked
    leftover = [
        p.name for p in tmp_path.iterdir()
        if p.name.startswith(".tmp_backup_") or p.name.startswith(".old_backup_")
    ]
    assert leftover == []
    db.close()


def test_backup_directory_sync_unsupported_platform_succeeds_gracefully(tmp_path):
    """
    On platforms or filesystems where directory fsync is unsupported (e.g. Windows, FAT/exFAT,
    returning EINVAL or ENOTSUP), backup() succeeds gracefully without raising,
    while file data contents remain fully durable.
    """
    db_path = tmp_path / "unsupported_sync_src"
    backup_path = tmp_path / "unsupported_sync_dst"

    db = ObjectDB(db_path)
    db.open()
    for i in range(10):
        db.put(i, f"payload_{i}".encode())

    # Simulate platform returning EINVAL or ENOTSUP on directory open/fsync
    def unsupported_sync(path):
        return False

    with patch("objectdb.database._sync_directory", side_effect=unsupported_sync):
        res = db.backup(backup_path)
        assert res == backup_path

    # Verify backup is valid and intact
    b = ObjectDB(backup_path)
    b.open()
    try:
        assert b.check_integrity()["valid"] is True
        for i in range(10):
            assert b.get(i) == f"payload_{i}".encode()
    finally:
        b.close()
        db.close()


def test_backup_parent_dir_fsync_failure_with_restoration_failure_preserves_old_backup(tmp_path):
    """
    When replacing a backup, if parent directory sync fails AND restoration rename fails:
    1. Both the sync error and restoration error are reported via exception chaining.
    2. The previous valid backup is NOT deleted and is preserved in .old_backup_<uuid>.
    3. The preserved backup remains functional and valid.
    """
    db_path = tmp_path / "double_sync_fail_src"
    backup_path = tmp_path / "double_sync_dst"

    db = ObjectDB(db_path)
    db.open()
    db.put(1, b"critical_old_backup")
    db.backup(backup_path)

    # Mutate source database
    db.put(2, b"new_data")

    sync_err = OSError(errno.EIO, "I/O error during parent directory sync")
    restore_rename_err = OSError(errno.EIO, "I/O error restoring old backup directory")

    original_sync = database._sync_directory
    original_rename = Path.rename

    def failing_sync(path):
        if Path(path) == backup_path.parent:
            raise sync_err
        return original_sync(path)

    def failing_rename(self, target):
        if Path(target) == backup_path and ".old_backup_" in str(self):
            raise restore_rename_err
        return original_rename(self, target)

    with patch("objectdb.database._sync_directory", side_effect=failing_sync):
        with patch.object(Path, "rename", side_effect=failing_rename, autospec=True):
            with pytest.raises(OSError, match="I/O error during parent directory sync") as exc_info:
                db.backup(backup_path)

            assert exc_info.value is sync_err
            assert exc_info.value.__cause__ is restore_rename_err

    # The old backup MUST NOT have been deleted!
    old_backups = [p for p in tmp_path.iterdir() if p.name.startswith(".old_backup_")]
    assert len(old_backups) == 1
    preserved_backup = old_backups[0]

    b_preserved = ObjectDB(preserved_backup)
    b_preserved.open()
    try:
        assert b_preserved.check_integrity()["valid"] is True
        assert b_preserved.get(1) == b"critical_old_backup"
        assert b_preserved.get(2) is None
    finally:
        b_preserved.close()
        db.close()


def test_backup_restoration_directory_sync_failure_reported_and_preserves_backup(tmp_path):
    """
    Test A: Restoration directory sync failure
    - Start with an existing valid backup.
    - Inject a failure during the initial replacement operation (staging rename fails).
    - Allow restoration of the previous backup to its original path to succeed.
    - Inject a genuine I/O error during directory synchronization after restoration.
    - Verify the error is reported via exception chaining rather than swallowed.
    - Verify the previous backup's contents remain recoverable at target_path.
    - Verify cleanup does not destroy the only recoverable backup.
    """
    db_path = tmp_path / "restore_sync_fail_src"
    backup_path = tmp_path / "restore_sync_fail_dst"

    db = ObjectDB(db_path)
    db.open()
    db.put(1, b"initial_valid_backup_data")
    db.backup(backup_path)

    # Verify initial backup is functional
    b1 = ObjectDB(backup_path)
    b1.open()
    assert b1.get(1) == b"initial_valid_backup_data"
    b1.close()

    # Mutate source db
    db.put(2, b"uncommitted_replacement_data")

    staging_rename_err = OSError(errno.EIO, "Injected staging rename failure")
    restoration_sync_err = OSError(errno.EIO, "Injected restoration directory sync failure")

    original_rename = Path.rename
    original_sync = database._sync_directory

    # Track when restoration rename has succeeded
    restoration_rename_completed = [False]

    def failing_rename(self, target):
        if Path(target) == backup_path and ".tmp_backup_" in str(self):
            # Step 1: Initial replacement rename fails
            raise staging_rename_err
        if Path(target) == backup_path and ".old_backup_" in str(self):
            # Step 2: Restoration rename succeeds
            res = original_rename(self, target)
            restoration_rename_completed[0] = True
            return res
        return original_rename(self, target)

    def failing_sync(path):
        if Path(path) == backup_path.parent and restoration_rename_completed[0]:
            # Step 3: Directory sync after restoration fails with genuine I/O error
            raise restoration_sync_err
        return original_sync(path)

    with patch.object(Path, "rename", side_effect=failing_rename, autospec=True):
        with patch("objectdb.database._sync_directory", side_effect=failing_sync):
            with pytest.raises(OSError, match="Injected staging rename failure") as exc_info:
                db.backup(backup_path)

            # Verify restoration sync failure is chained and NOT swallowed
            assert exc_info.value is staging_rename_err
            assert exc_info.value.__cause__ is restoration_sync_err

    # Verify the previous backup's contents remain recoverable at backup_path
    assert backup_path.exists(), "Previous backup should be preserved at backup_path"
    b_recovered = ObjectDB(backup_path)
    b_recovered.open()
    try:
        assert b_recovered.check_integrity()["valid"] is True
        assert b_recovered.get(1) == b"initial_valid_backup_data"
        assert b_recovered.get(2) is None
    finally:
        b_recovered.close()

    # Verify staging cleanup did not leave stray files
    leftover = [
        p.name for p in tmp_path.iterdir()
        if p.name.startswith(".tmp_backup_") or p.name.startswith(".old_backup_")
    ]
    assert leftover == []
    db.close()


def test_sync_directory_unsupported_errnos_return_false(tmp_path):
    """
    Unsupported directory synchronization errnos (EINVAL, ENOTSUP, ENOSYS)
    return False without raising exceptions.
    """
    for code in (errno.EINVAL, errno.ENOTSUP, errno.ENOSYS):
        with patch("os.fsync", side_effect=OSError(code, "Unsupported operation")):
            result = database._sync_directory(tmp_path)
            assert result is False, f"Expected False for errno {code}"


def test_sync_directory_genuine_io_errnos_raise(tmp_path):
    """
    Genuine filesystem and hardware I/O errnos (EIO, ENOSPC, EROFS)
    must propagate and raise OSError.
    """
    for code in (errno.EIO, errno.ENOSPC, errno.EROFS):
        with patch("os.fsync", side_effect=OSError(code, "Hardware error")):
            with pytest.raises(OSError) as exc_info:
                database._sync_directory(tmp_path)
            assert exc_info.value.errno == code


def test_sync_directory_permission_denied_raises_on_posix(tmp_path):
    """
    On POSIX systems, a genuine permission denial (EACCES / EPERM) on os.open()
    must raise PermissionError and never be misclassified as unsupported.
    """
    perm_err = PermissionError(errno.EACCES, "Permission denied")
    with patch("sys.platform", "darwin"):
        with patch("os.name", "posix"):
            with patch("os.open", side_effect=perm_err):
                with pytest.raises(PermissionError) as exc_info:
                    database._sync_directory(tmp_path)
                assert exc_info.value is perm_err


def test_sync_directory_windows_platform_limitation_vs_actual_permission_denial(tmp_path):
    """
    On Windows (NT), distinguish between:
    1. The known CRT limitation (EACCES on directory open, but directory is readable): returns False.
    2. Genuine access-denied failure (EACCES on directory open, AND scandir fails): raises PermissionError.
    """
    perm_err = PermissionError(errno.EACCES, "Access is denied")

    # Case 1: Windows CRT limitation (directory is accessible via scandir)
    with patch("sys.platform", "win32"):
        with patch("os.name", "nt"):
            with patch("os.open", side_effect=perm_err):
                # When os.scandir succeeds, the directory is readable; return False
                result = database._sync_directory(tmp_path)
                assert result is False

    # Case 2: Genuine access denial on Windows (scandir also fails with PermissionError)
    with patch("sys.platform", "win32"):
        with patch("os.name", "nt"):
            with patch("os.open", side_effect=perm_err):
                with patch("os.scandir", side_effect=perm_err):
                    with pytest.raises(PermissionError) as exc_info:
                        database._sync_directory(tmp_path)
                    assert exc_info.value is perm_err




