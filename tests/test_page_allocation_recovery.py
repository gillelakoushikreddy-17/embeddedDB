import os
from pathlib import Path
import pytest
from objectdb import ObjectDB
from objectdb.page_manager import PageManager, PAGE_SIZE
from objectdb.wal import WriteAheadLog
from objectdb.transactional_page_writer import TransactionalPageWriter
from objectdb.recovery import RecoveryManager
from objectdb.transaction import Transaction


def test_crash_after_page_allocation_reclaims_orphaned_pages_on_reopen(tmp_path):
    """
    If a transaction allocates pages and then crashes before committing,
    startup recovery physically reclaims the orphaned pages by truncating the file.
    """
    db_path = tmp_path / "alloc_crash_db"
    db = ObjectDB(db_path)
    db.open()

    # Initial state: insert 10 keys so tree and pages are established
    for i in range(10):
        db.put(i, f"initial_{i}".encode("ascii"))

    db.page_manager.file.seek(0, 2)
    initial_page_count = db.page_manager.file.tell() // PAGE_SIZE
    assert initial_page_count >= 2  # header + root leaf

    # Begin explicit transaction and allocate 3 new pages
    try:
        with db.transaction() as tx:
            p1 = tx.allocate_page()
            p2 = tx.allocate_page()
            p3 = tx.allocate_page()
            assert p1 >= initial_page_count
            assert p3 == p1 + 2

            # Simulate ungraceful crash without committing or rolling back
            db.simulate_crash()
            raise SystemExit("Crash simulated")
    except SystemExit:
        pass

    # Inspect the file before recovery: orphaned pages physically exist on disk
    with open(db_path / "objectdb.data", "rb") as fp:
        fp.seek(0, 2)
        crashed_page_count = fp.tell() // PAGE_SIZE

    assert crashed_page_count == initial_page_count + 3, (
        f"Expected {initial_page_count + 3} pages before recovery, found {crashed_page_count}"
    )

    # Reopen database: startup recovery must physically reclaim the 3 orphaned pages
    reopened_db = ObjectDB(db_path)
    reopened_db.open()

    try:
        reopened_db.page_manager.file.seek(0, 2)
        recovered_page_count = reopened_db.page_manager.file.tell() // PAGE_SIZE
        assert recovered_page_count == initial_page_count, (
            f"Expected {initial_page_count} pages after recovery, but found {recovered_page_count}"
        )

        # Integrity is valid and initial data is intact
        assert reopened_db.check_integrity()["valid"] is True
        for i in range(10):
            assert reopened_db.get(i) == f"initial_{i}".encode("ascii")

        # Next allocation reuses the reclaimed page ID
        with reopened_db.transaction() as tx:
            next_page = tx.allocate_page()
            assert next_page == initial_page_count

    finally:
        reopened_db.close()


def test_crash_during_page_allocation_before_file_write(tmp_path):
    """
    If a crash occurs after PAGE_ALLOCATED is written to WAL but before
    allocate_page() appends the page to objectdb.data, recovery cleanly
    tolerates the missing page without crashing.
    """
    db_path = tmp_path / "alloc_prewrite_crash_db"
    db = ObjectDB(db_path)
    db.open()
    db.put(1, b"val1")

    db.page_manager.file.seek(0, 2)
    expected_page_count = db.page_manager.file.tell() // PAGE_SIZE

    # Write BEGIN and PAGE_ALLOCATED records directly to WAL simulating crash before file write
    db.wal.append("BEGIN", {"transaction_id": 999})
    db.wal.append("PAGE_ALLOCATED", {"transaction_id": 999, "page_id": expected_page_count})
    db.wal.file.flush()

    db.simulate_crash()

    # Reopen: recovery should see PAGE_ALLOCATED for page that was never written
    reopened_db = ObjectDB(db_path)
    reopened_db.open()

    try:
        reopened_db.page_manager.file.seek(0, 2)
        assert reopened_db.page_manager.file.tell() // PAGE_SIZE == expected_page_count
        assert reopened_db.get(1) == b"val1"
    finally:
        reopened_db.close()


def test_repeated_recovery_with_reclaimed_pages_is_idempotent(tmp_path):
    """
    Running recovery repeatedly on a database with reclaimed pages
    does not error and leaves page allocation and metadata consistent.
    """
    db_path = tmp_path / "repeated_rec_db"
    db = ObjectDB(db_path)
    db.open()
    db.put(1, b"v1")

    db.page_manager.file.seek(0, 2)
    base_page_count = db.page_manager.file.tell() // PAGE_SIZE

    try:
        with db.transaction() as tx:
            tx.allocate_page()
            tx.allocate_page()
            db.simulate_crash()
            raise SystemExit("Crash simulated")
    except SystemExit:
        pass

    # First recovery
    db1 = ObjectDB(db_path)
    db1.open()
    assert db1.page_manager.page_count == base_page_count
    db1.close()

    # Second recovery (simulating restart or repeated recovery)
    db2 = ObjectDB(db_path)
    db2.open()
    try:
        assert db2.page_manager.page_count == base_page_count
        assert db2.get(1) == b"v1"
        assert db2.check_integrity()["valid"] is True
    finally:
        db2.close()


def test_transaction_rollback_reclaims_allocated_pages(tmp_path):
    """
    When a transaction explicitly rolls back after allocating pages,
    the newly allocated pages are physically reclaimed from disk.
    """
    db_path = tmp_path / "rollback_alloc_db"
    db = ObjectDB(db_path)
    db.open()
    db.put(1, b"hello")

    initial_page_count = db.page_manager.page_count

    try:
        with db.transaction() as tx:
            p1 = tx.allocate_page()
            p2 = tx.allocate_page()
            assert db.page_manager.page_count == initial_page_count + 2
            raise RuntimeError("Intentional abort to trigger rollback")
    except RuntimeError:
        pass

    # Pages must be reclaimed immediately upon rollback
    assert db.page_manager.page_count == initial_page_count

    # Subsequent writes succeed normally
    db.put(2, b"world")
    assert db.get(1) == b"hello"
    assert db.get(2) == b"world"
    db.close()
