import pytest
from objectdb import ObjectDB
from objectdb.transaction import TransactionState


def test_durability_ordering_segment_synced_before_commit(tmp_path):
    """
    Verify that payload data is synced to the segment before metadata COMMIT record is written.
    """
    db_path = tmp_path / "test_db"
    db = ObjectDB(db_path)
    db.open()

    order_of_operations = []

    original_segment_sync = db.segment.sync
    def tracked_segment_sync():
        order_of_operations.append("segment_sync")
        return original_segment_sync()

    original_wal_append = db.wal.append
    def tracked_wal_append(record_type, data):
        order_of_operations.append(f"wal_{record_type}")
        return original_wal_append(record_type, data)

    db.segment.sync = tracked_segment_sync
    db.wal.append = tracked_wal_append

    db.put(100, b"important_payload")

    # Verify that segment_sync occurred BEFORE wal_COMMIT
    assert "segment_sync" in order_of_operations
    assert "wal_COMMIT" in order_of_operations

    seg_idx = order_of_operations.index("segment_sync")
    commit_idx = order_of_operations.index("wal_COMMIT")
    assert seg_idx < commit_idx, f"Segment sync ({seg_idx}) must precede WAL COMMIT ({commit_idx})"

    db.close()


def test_explicit_transaction_segment_synced_before_commit(tmp_path):
    """
    Verify that in explicit multi-operation transactions, segment sync precedes WAL COMMIT.
    """
    db_path = tmp_path / "test_db"
    db = ObjectDB(db_path)
    db.open()

    order_of_operations = []

    original_segment_sync = db.segment.sync
    def tracked_segment_sync():
        order_of_operations.append("segment_sync")
        return original_segment_sync()

    original_wal_append = db.wal.append
    def tracked_wal_append(record_type, data):
        order_of_operations.append(f"wal_{record_type}")
        return original_wal_append(record_type, data)

    db.segment.sync = tracked_segment_sync
    db.wal.append = tracked_wal_append

    with db.transaction():
        db.put(1, b"val1")
        db.put(2, b"val2")

    assert "segment_sync" in order_of_operations
    assert "wal_COMMIT" in order_of_operations

    last_seg_idx = len(order_of_operations) - 1 - order_of_operations[::-1].index("segment_sync")
    commit_idx = order_of_operations.index("wal_COMMIT")
    assert last_seg_idx < commit_idx, "All segment data must be synced before WAL COMMIT"

    db.close()


def test_failure_at_segment_sync_rolls_back_cleanly(tmp_path):
    """
    If segment synchronization fails, the transaction rolls back cleanly
    without committing metadata or leaving poisoned state.
    """
    db_path = tmp_path / "test_db"
    db = ObjectDB(db_path)
    db.open()

    def failing_sync():
        raise OSError("Disk I/O failure during segment fsync")

    db.segment.sync = failing_sync

    with pytest.raises(OSError, match="Disk I/O failure"):
        db.put(50, b"failed_payload")

    # Transaction rolled back cleanly; key 50 should not exist
    db.segment.sync = lambda: None  # restore sync
    assert db.get(50) is None

    # Normal put should still work
    db.put(51, b"success_payload")
    assert db.get(51) == b"success_payload"

    db.close()


def test_commit_io_failure_poisons_database_and_prevents_unsafe_rollback(tmp_path):
    """
    When commit fails at the WAL append stage, the outcome is uncertain.
    The transaction must transition to FAILED, reject rollback(), and poison the db instance.
    """
    db_path = tmp_path / "test_db"
    db = ObjectDB(db_path)
    db.open()

    # Pre-populate one item
    db.put(1, b"first")

    original_append = db.wal.append
    def failing_append(record_type, data):
        if record_type == "COMMIT":
            raise OSError("I/O error while writing COMMIT record to WAL")
        return original_append(record_type, data)

    db.wal.append = failing_append

    with pytest.raises(OSError, match="I/O error while writing COMMIT record"):
        db.put(2, b"second")

    assert db.is_poisoned

    # Any subsequent operation on the poisoned instance must be rejected
    with pytest.raises(RuntimeError, match="poisoned state"):
        db.get(1)

    with pytest.raises(RuntimeError, match="poisoned state"):
        db.put(3, b"third")

    db.close()

    # Reopening triggers startup recovery and brings the database to a clean consistent state
    reopened_db = ObjectDB(db_path)
    reopened_db.open()
    try:
        assert reopened_db.get(1) == b"first"
        assert reopened_db.get(2) is None
    finally:
        reopened_db.close()


def test_key_range_validation(tmp_path):
    """
    Verify that 64-bit signed integer bounds [-2^63, 2^63 - 1] are enforced.
    """
    db_path = tmp_path / "test_db"
    db = ObjectDB(db_path)
    db.open()

    min_key = -9223372036854775808
    max_key = 9223372036854775807

    # Valid boundary keys work
    db.put(min_key, b"min")
    db.put(max_key, b"max")
    assert db.get(min_key) == b"min"
    assert db.get(max_key) == b"max"

    # Out of range keys rejected with ValueError
    with pytest.raises(ValueError, match="out of range"):
        db.put(min_key - 1, b"underflow")

    with pytest.raises(ValueError, match="out of range"):
        db.put(max_key + 1, b"overflow")

    # Non-integer / boolean keys rejected with TypeError
    with pytest.raises(TypeError, match="must be an integer"):
        db.put(True, b"bool")

    with pytest.raises(TypeError, match="must be an integer"):
        db.put("key", b"string")

    with pytest.raises(TypeError, match="must be bytes"):
        db.put(10, "not bytes")

    db.close()
