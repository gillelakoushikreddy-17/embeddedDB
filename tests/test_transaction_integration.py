import pytest
from objectdb import ObjectDB
from objectdb.header import read_header
from objectdb.page_manager import PAGE_SIZE


def test_put_creates_committed_transaction_in_wal(tmp_path):
    db_path = tmp_path / "test_db"
    db = ObjectDB(db_path)
    db.open()

    try:
        db.put(1, b"first_value")
        assert db.get(1) == b"first_value"

        # Verify WAL records for the transaction
        records = db.wal.read_all()
        types = [r["type"] for r in records]
        assert "BEGIN" in types
        assert "COMMIT" in types
        # All records should have transaction_id == 1
        for r in records:
            assert r["data"]["transaction_id"] == 1
    finally:
        db.close()


def test_failed_mutation_rolls_back_pages_and_metadata(tmp_path):
    db_path = tmp_path / "test_db"
    db = ObjectDB(db_path)
    db.open()

    try:
        db.put(1, b"original_value")
        assert db.get(1) == b"original_value"

        # Inject failure during tree insertion
        original_insert = db.tree.insert_into_root

        def failing_insert(key, record):
            # Perform partial work then fail
            original_insert(key, record)
            raise RuntimeError("Injected failure during insert")

        db.tree.insert_into_root = failing_insert

        with pytest.raises(RuntimeError, match="Injected failure during insert"):
            db.put(1, b"new_failed_value")

        # Tree and record must have rolled back to original_value
        db.tree.insert_into_root = original_insert
        assert db.get(1) == b"original_value"

        # Verify WAL contains a ROLLBACK record
        records = db.wal.read_all()
        assert records[-1]["type"] == "ROLLBACK"
        assert records[-1]["data"]["transaction_id"] == 2
    finally:
        db.close()


def test_rollback_restores_root_page_after_split(tmp_path):
    """Verify that if a mutation causes a root split and then fails, root page ID reverts."""
    db_path = tmp_path / "test_db"
    db = ObjectDB(db_path)
    db.open()

    try:
        # Fill root leaf close to capacity without splitting
        # Leaf capacity is (4096 - 3) // (8 + 20) = 4093 // 28 = 146
        capacity = (PAGE_SIZE - 3) // 28
        for i in range(capacity):
            db.put(i, f"val_{i}".encode("ascii"))

        header_before = read_header(db.page_manager.read_page(0))
        old_root_id = header_before["root_page_id"]
        assert db.tree.root_page_id == old_root_id

        # Next put would cause a split. Let's make commit fail to test rollback of split
        original_commit = db._begin_transaction

        class FailingTransaction:
            def __init__(self, real_tx):
                self._real = real_tx

            def __getattr__(self, name):
                return getattr(self._real, name)

            def commit(self):
                # Split has occurred in PersistentBTree, now fail commit
                raise RuntimeError("Injected commit failure")

        def failing_begin():
            tx = original_commit()
            return FailingTransaction(tx)

        db._begin_transaction = failing_begin

        with pytest.raises(RuntimeError, match="Injected commit failure"):
            db.put(capacity + 1, b"trigger_split")

        # Restore original begin_transaction
        db._begin_transaction = original_commit

        # Header on disk and tree in memory must be restored to old_root_id
        header_after = read_header(db.page_manager.read_page(0))
        assert header_after["root_page_id"] == old_root_id
        assert db.tree.root_page_id == old_root_id

        # Data from rolled back put is not visible
        assert db.get(capacity + 1) is None
        # Previous data is intact
        for i in range(capacity):
            assert db.get(i) == f"val_{i}".encode("ascii")
    finally:
        db.close()


def test_page_allocation_metadata_remains_consistent_after_rollback(tmp_path):
    db_path = tmp_path / "test_db"
    db = ObjectDB(db_path)
    db.open()

    try:
        db.put(1, b"val1")

        # Inject failure during put
        original_append = db.segment.append

        def failing_append(val):
            offset, size = original_append(val)
            raise RuntimeError("Injected failure after segment append")

        db.segment.append = failing_append

        with pytest.raises(RuntimeError, match="Injected failure after segment append"):
            db.put(2, b"val2")

        db.segment.append = original_append

        # Verify page manager file is still aligned to PAGE_SIZE
        db.page_manager.file.seek(0, 2)
        file_size = db.page_manager.file.tell()
        assert file_size % PAGE_SIZE == 0

        # Further allocations and writes succeed
        db.put(3, b"val3")
        assert db.get(3) == b"val3"
        assert db.get(1) == b"val1"
        assert db.get(2) is None
    finally:
        db.close()


def test_repeated_writes_to_same_key_in_separate_transactions(tmp_path):
    db_path = tmp_path / "test_db"
    db = ObjectDB(db_path)
    db.open()

    try:
        db.put(1, b"v1")
        assert db.get(1) == b"v1"

        db.put(1, b"v2")
        assert db.get(1) == b"v2"

        db.put(1, b"v3")
        assert db.get(1) == b"v3"
    finally:
        db.close()

    # Reopen to confirm final value persists
    db = ObjectDB(db_path)
    db.open()
    try:
        assert db.get(1) == b"v3"
    finally:
        db.close()


def test_transaction_isolation_rollback_does_not_affect_committed(tmp_path):
    db_path = tmp_path / "test_db"
    db = ObjectDB(db_path)
    db.open()

    try:
        db.put(1, b"committed_1")
        db.put(2, b"committed_2")

        # Now fail tx 3
        original_insert = db.tree.insert_into_root

        def failing_insert(key, record):
            raise RuntimeError("Failure in tx 3")

        db.tree.insert_into_root = failing_insert

        with pytest.raises(RuntimeError):
            db.put(3, b"uncommitted_3")

        db.tree.insert_into_root = original_insert

        # Tx 1 and 2 must remain completely intact
        assert db.get(1) == b"committed_1"
        assert db.get(2) == b"committed_2"
        assert db.get(3) is None
    finally:
        db.close()


def test_explicit_transaction_context_commits_all(tmp_path):
    db_path = tmp_path / "test_db"
    db = ObjectDB(db_path)
    db.open()

    try:
        with db.transaction():
            db.put(10, b"batch_1")
            db.put(20, b"batch_2")
            db.put(30, b"batch_3")

        assert db.get(10) == b"batch_1"
        assert db.get(20) == b"batch_2"
        assert db.get(30) == b"batch_3"
    finally:
        db.close()

    # Reopen and check
    db = ObjectDB(db_path)
    db.open()
    try:
        assert db.get(10) == b"batch_1"
        assert db.get(20) == b"batch_2"
        assert db.get(30) == b"batch_3"
    finally:
        db.close()


def test_explicit_transaction_context_rolls_back_all_on_exception(tmp_path):
    db_path = tmp_path / "test_db"
    db = ObjectDB(db_path)
    db.open()

    try:
        db.put(1, b"existing")

        with pytest.raises(ValueError, match="Abort batch"):
            with db.transaction():
                db.put(10, b"new_10")
                db.put(20, b"new_20")
                raise ValueError("Abort batch")

        # new_10 and new_20 must not exist
        assert db.get(10) is None
        assert db.get(20) is None
        assert db.get(1) == b"existing"
    finally:
        db.close()
