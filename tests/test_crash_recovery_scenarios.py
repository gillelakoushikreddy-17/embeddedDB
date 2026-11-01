import pytest
from embeddeddb import EmbeddedDB
from embeddeddb.header import read_header
from embeddeddb.page_manager import PAGE_SIZE, PageManager
from embeddeddb.wal import WriteAheadLog
from embeddeddb.transaction import Transaction, TransactionPageAdapter
from embeddeddb.transactional_page_writer import TransactionalPageWriter
from embeddeddb.recovery import RecoveryManager


def test_reopen_after_committed_transaction(tmp_path):
    db_path = tmp_path / "test_db"
    db = EmbeddedDB(db_path)
    db.open()
    db.put(42, b"answer")
    db.close()

    # Reopen
    db2 = EmbeddedDB(db_path)
    db2.open()
    try:
        assert db2.get(42) == b"answer"
    finally:
        db2.close()


def test_startup_recovery_undoes_uncommitted_put(tmp_path):
    db_path = tmp_path / "test_db"
    db = EmbeddedDB(db_path)
    db.open()
    db.put(1, b"committed_data")

    # Start a transaction manually without committing (simulating a crash)
    tx = db._begin_transaction()
    adapter = TransactionPageAdapter(tx, db.page_manager, db.tree)
    old_adapter = db.tree.page_manager
    db.tree.page_manager = adapter
    try:
        offset, size = db.segment.append(b"uncommitted_data")
        from embeddeddb.record import encode_record
        db.tree.insert_into_root(2, encode_record(0, offset, size))
    finally:
        db.tree.page_manager = old_adapter

    # Crash: close without tx.commit() or tx.rollback()
    db.segment.close()
    db.page_manager.close()
    db.wal.close()
    db.is_open = False

    # Reopen database - recovery should run automatically
    recovered_db = EmbeddedDB(db_path)
    recovered_db.open()
    try:
        assert recovered_db.get(1) == b"committed_data"
        assert recovered_db.get(2) is None
    finally:
        recovered_db.close()


def test_startup_recovery_undoes_uncommitted_delete(tmp_path):
    db_path = tmp_path / "test_db"
    db = EmbeddedDB(db_path)
    db.open()
    db.put(1, b"keep_me")

    # Start an uncommitted delete
    tx = db._begin_transaction()
    adapter = TransactionPageAdapter(tx, db.page_manager, db.tree)
    old_adapter = db.tree.page_manager
    db.tree.page_manager = adapter
    try:
        db.tree.delete(1)
    finally:
        db.tree.page_manager = old_adapter

    # Simulate crash before commit
    db.segment.close()
    db.page_manager.close()
    db.wal.close()
    db.is_open = False

    # Reopen - startup recovery should restore deleted key
    recovered_db = EmbeddedDB(db_path)
    recovered_db.open()
    try:
        assert recovered_db.get(1) == b"keep_me"
    finally:
        recovered_db.close()


def test_startup_recovery_after_interrupted_tree_split(tmp_path):
    """Simulate a crash during a root split before the transaction commits."""
    db_path = tmp_path / "test_db"
    db = EmbeddedDB(db_path)
    db.open()

    # Fill root leaf close to capacity
    capacity = (PAGE_SIZE - 3) // 28
    for i in range(capacity):
        db.put(i, f"v_{i}".encode("ascii"))

    header_before = read_header(db.page_manager.read_page(0))
    initial_root_id = header_before["root_page_id"]

    # Start a transaction that causes a split
    tx = db._begin_transaction()
    adapter = TransactionPageAdapter(tx, db.page_manager, db.tree)
    old_adapter = db.tree.page_manager
    db.tree.page_manager = adapter
    try:
        offset, size = db.segment.append(b"split_trigger")
        from embeddeddb.record import encode_record
        db.tree.insert_into_root(capacity + 5, encode_record(0, offset, size))
    finally:
        db.tree.page_manager = old_adapter

    # Root should have split in memory and page 0 should have been modified
    header_split = read_header(db.page_manager.read_page(0))
    assert header_split["root_page_id"] != initial_root_id

    # Crash! Close files without commit or rollback
    db.segment.close()
    db.page_manager.close()
    db.wal.close()
    db.is_open = False

    # Reopen database - recovery must restore root page ID and all pages
    recovered_db = EmbeddedDB(db_path)
    recovered_db.open()
    try:
        header_recovered = read_header(recovered_db.page_manager.read_page(0))
        assert header_recovered["root_page_id"] == initial_root_id
        assert recovered_db.tree.root_page_id == initial_root_id

        # Original keys are present
        for i in range(capacity):
            assert recovered_db.get(i) == f"v_{i}".encode("ascii")

        # Split key is absent
        assert recovered_db.get(capacity + 5) is None
    finally:
        recovered_db.close()


def test_startup_recovery_handles_truncated_final_wal_record(tmp_path):
    db_path = tmp_path / "test_db"
    db = EmbeddedDB(db_path)
    db.open()
    db.put(1, b"first")
    db.put(2, b"second")
    db.close()

    # Simulate an incomplete/truncated record written right at crash time
    wal_file = db_path / "embeddeddb.wal"
    with open(wal_file, "ab") as f:
        f.write(b'{"lsn":99,"type":"PAGE_BEFORE_IMAGE","data":{"trans')

    # Reopen should discard the incomplete final record and open cleanly
    db2 = EmbeddedDB(db_path)
    db2.open()
    try:
        assert db2.get(1) == b"first"
        assert db2.get(2) == b"second"

        # Subsequent writes should continue normally
        db2.put(3, b"third")
        assert db2.get(3) == b"third"
    finally:
        db2.close()


def test_recovery_is_idempotent(tmp_path):
    db_path = tmp_path / "test_db"
    wal_path = tmp_path / "test.wal"

    manager = PageManager(db_path)
    manager.initialize()
    manager.open()
    p1 = manager.allocate_page()
    manager.write_page(p1, b"A" * PAGE_SIZE)
    manager.sync()

    wal = WriteAheadLog(wal_path)
    wal.open()

    writer = TransactionalPageWriter(manager, wal)
    tx = Transaction(1, wal, writer)
    tx.write_page(p1, b"B" * PAGE_SIZE)

    # Crash without commit
    manager.close()
    wal.close()

    # Reopen and run recovery twice
    m2 = PageManager(db_path)
    m2.open()
    w2 = WriteAheadLog(wal_path)
    w2.open()
    wr2 = TransactionalPageWriter(m2, w2)
    rec = RecoveryManager(w2)

    res1 = rec.recover(wr2)
    assert m2.read_page(p1) == b"A" * PAGE_SIZE
    assert res1["recovered_transactions"] == [1]

    res2 = rec.recover(wr2)
    assert m2.read_page(p1) == b"A" * PAGE_SIZE
    assert res2["recovered_transactions"] == [1]

    m2.close()
    w2.close()


def test_recovered_transaction_does_not_undo_subsequent_committed_transactions_on_next_reopen(tmp_path):
    """
    Verify that an incomplete transaction recovered in session 1 is recorded as rolled back,
    so that subsequent session 2 updates to the same pages/keys are NOT undone when session 3 opens.
    """
    db_path = tmp_path / "multi_session_recovery_db"

    # Session 1: Commit initial value
    db = EmbeddedDB(db_path)
    db.open()
    db.put(1, b"session1_val")
    db.close()

    # Session 2: Start transaction, write dirty value, simulate ungraceful crash
    db = EmbeddedDB(db_path)
    db.open()
    try:
        with db.transaction():
            db.put(1, b"dirty_uncommitted")
            db.simulate_crash()
            raise SystemExit("Crash")
    except SystemExit:
        pass

    # Session 3: Reopen database. Startup recovery rolls back dirty value to session1_val
    db = EmbeddedDB(db_path)
    db.open()
    assert db.get(1) == b"session1_val"

    # Commit a new update to the same key
    db.put(1, b"session3_committed_val")
    assert db.get(1) == b"session3_committed_val"
    db.close()

    # Session 4: Reopen database. Recovery MUST NOT re-undo session 3's committed write!
    db = EmbeddedDB(db_path)
    db.open()
    try:
        assert db.get(1) == b"session3_committed_val"
        assert db.check_integrity()["valid"] is True
    finally:
        db.close()

