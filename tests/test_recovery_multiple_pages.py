from objectdb.page_manager import PageManager, PAGE_SIZE
from objectdb.wal import WriteAheadLog
from objectdb.transaction import Transaction
from objectdb.transactional_page_writer import TransactionalPageWriter
from objectdb.recovery import RecoveryManager


def test_recovery_restores_multiple_pages(tmp_path):
    db_path = tmp_path / "test.db"
    wal_path = tmp_path / "test.wal"

    # 1. Create the database.
    manager = PageManager(db_path)
    manager.initialize()
    manager.open()

    page_id_1 = manager.allocate_page()
    page_id_2 = manager.allocate_page()

    # 2. Store the original contents.
    original_page_1 = b"A" * PAGE_SIZE
    original_page_2 = b"B" * PAGE_SIZE

    manager.write_page(page_id_1, original_page_1)
    manager.write_page(page_id_2, original_page_2)
    manager.sync()

    # 3. Open the WAL and begin a transaction.
    wal = WriteAheadLog(wal_path)
    wal.open()

    writer = TransactionalPageWriter(manager, wal)
    transaction = Transaction(1, wal, writer)

    # 4. Modify both pages without committing.
    transaction.write_page(page_id_1, b"X" * PAGE_SIZE)
    transaction.write_page(page_id_2, b"Y" * PAGE_SIZE)

    assert manager.read_page(page_id_1) == b"X" * PAGE_SIZE
    assert manager.read_page(page_id_2) == b"Y" * PAGE_SIZE

    # 5. Simulate an interrupted transaction.
    manager.close()
    wal.close()

    # 6. Reopen the files.
    manager = PageManager(db_path)
    manager.open()

    wal = WriteAheadLog(wal_path)
    wal.open()

    writer = TransactionalPageWriter(manager, wal)
    recovery = RecoveryManager(wal)

    # 7. Recover the incomplete transaction.
    result = recovery.recover(writer)

    # 8. Verify both pages were restored.
    assert manager.read_page(page_id_1) == original_page_1
    assert manager.read_page(page_id_2) == original_page_2

    assert result["recovered_transactions"] == [1]
    assert result["restored_pages"] == 2

    manager.close()
    wal.close()