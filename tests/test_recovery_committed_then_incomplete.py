from embeddeddb.page_manager import PageManager, PAGE_SIZE
from embeddeddb.wal import WriteAheadLog
from embeddeddb.transaction import Transaction
from embeddeddb.transactional_page_writer import TransactionalPageWriter
from embeddeddb.recovery import RecoveryManager


def test_recovery_preserves_commit_and_undoes_later_transaction(tmp_path):
    db_path = tmp_path / "test.db"
    wal_path = tmp_path / "test.wal"

    # 1. Create the database.
    manager = PageManager(db_path)
    manager.initialize()
    manager.open()

    page_id = manager.allocate_page()

    original_page = b"A" * PAGE_SIZE
    committed_page = b"B" * PAGE_SIZE
    uncommitted_page = b"C" * PAGE_SIZE

    manager.write_page(page_id, original_page)
    manager.sync()

    wal = WriteAheadLog(wal_path)
    wal.open()

    writer = TransactionalPageWriter(manager, wal)

    # 2. Transaction 1 changes A -> B and commits.
    tx1 = Transaction(1, wal, writer)
    tx1.write_page(page_id, committed_page)
    tx1.commit()

    assert manager.read_page(page_id) == committed_page

    # 3. Transaction 2 changes B -> C but does not commit.
    tx2 = Transaction(2, wal, writer)
    tx2.write_page(page_id, uncommitted_page)

    assert manager.read_page(page_id) == uncommitted_page

    # 4. Simulate an interrupted transaction.
    manager.close()
    wal.close()

    # 5. Reopen and recover.
    manager = PageManager(db_path)
    manager.open()

    wal = WriteAheadLog(wal_path)
    wal.open()

    writer = TransactionalPageWriter(manager, wal)
    recovery = RecoveryManager(wal)

    result = recovery.recover(writer)

    # 6. The committed value B must survive.
    assert manager.read_page(page_id) == committed_page
    assert result["recovered_transactions"] == [2]

    manager.close()
    wal.close()