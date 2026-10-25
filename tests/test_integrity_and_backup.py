import pytest
from objectdb import ObjectDB
from objectdb.page_manager import PAGE_SIZE


def test_integrity_check_on_empty_and_populated_db(tmp_path):
    """Integrity check passes on both freshly initialized and populated databases."""
    db_path = tmp_path / "test_db"
    db = ObjectDB(db_path)
    db.open()

    # Empty database
    report = db.check_integrity(raise_on_error=True)
    assert report["valid"] is True
    assert report["total_keys"] == 0
    assert report["errors"] == []

    # Populate 100 items
    for i in range(100):
        db.put(i, f"payload_{i}".encode("utf-8"))

    report = db.check_integrity(raise_on_error=True)
    assert report["valid"] is True
    assert report["total_keys"] == 100
    assert report["reachable_pages"] > 1
    assert report["errors"] == []

    db.close()


def test_integrity_check_detects_corrupted_key_ordering(tmp_path):
    """Integrity check detects an out-of-order key inside a leaf page."""
    db_path = tmp_path / "test_db"
    db = ObjectDB(db_path)
    db.open()

    for i in range(10):
        db.put(i, f"val_{i}".encode("ascii"))

    root_page_id = db.tree.root_page_id
    from objectdb.btree import decode_leaf_node, LEAF_HEADER_FORMAT, LEAF_NODE_TYPE, KEY_FORMAT, RECORD_SIZE, KEY_SIZE, LEAF_HEADER_SIZE
    import struct

    keys, records = decode_leaf_node(db.page_manager.read_page(root_page_id))
    keys[0], keys[1] = keys[1], keys[0]  # swap keys to violate sort order

    # Pack manually without validation to inject corruption
    corrupt_page = bytearray(PAGE_SIZE)
    struct.pack_into(LEAF_HEADER_FORMAT, corrupt_page, 0, LEAF_NODE_TYPE, len(keys))
    offset = LEAF_HEADER_SIZE
    for k, r in zip(keys, records):
        struct.pack_into(KEY_FORMAT, corrupt_page, offset, k)
        offset += KEY_SIZE
        corrupt_page[offset:offset + RECORD_SIZE] = r
        offset += RECORD_SIZE

    # Overwrite page directly to inject corruption
    db.page_manager.write_page(root_page_id, bytes(corrupt_page))

    report = db.check_integrity(raise_on_error=False)
    assert report["valid"] is False
    assert any("sorted" in err.lower() for err in report["errors"])

    with pytest.raises(ValueError, match="Integrity check failed"):
        db.check_integrity(raise_on_error=True)

    db.close()


def test_backup_creates_consistent_independent_copy(tmp_path):
    """Backup produces a consistent, verifiable replica that can be modified independently."""
    db_path = tmp_path / "source_db"
    backup_path = tmp_path / "backup_db"

    db = ObjectDB(db_path)
    db.open()

    for i in range(50):
        db.put(i, f"content_{i}".encode("ascii"))

    # Perform backup
    res = db.backup(backup_path)
    assert res == backup_path

    # Verify backup files exist
    assert (backup_path / "objectdb.data").exists()
    assert (backup_path / "segment-0.dat").exists()
    assert (backup_path / "objectdb.wal").exists()

    # Modify source db after backup
    db.put(999, b"source_only")
    db.close()

    # Open backup db and verify integrity
    backup_db = ObjectDB(backup_path)
    backup_db.open()
    try:
        report = backup_db.check_integrity(raise_on_error=True)
        assert report["valid"] is True
        assert report["total_keys"] == 50

        for i in range(50):
            assert backup_db.get(i) == f"content_{i}".encode("ascii")

        # 999 was inserted in source after backup, so it must not be in backup
        assert backup_db.get(999) is None

        # Verify mutations on backup work and do not touch source
        backup_db.put(1000, b"backup_only")
        assert backup_db.get(1000) == b"backup_only"
    finally:
        backup_db.close()


def test_backup_during_active_transaction_rejected(tmp_path):
    """Backup is rejected if an explicit transaction is currently in progress."""
    db_path = tmp_path / "source_db"
    backup_path = tmp_path / "backup_db"

    db = ObjectDB(db_path)
    db.open()

    with db.transaction():
        db.put(1, b"val1")
        with pytest.raises(RuntimeError, match="active transaction"):
            db.backup(backup_path)

    db.close()
