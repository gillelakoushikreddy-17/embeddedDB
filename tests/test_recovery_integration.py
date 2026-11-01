from embeddeddb.page_manager import PageManager, PAGE_SIZE
from embeddeddb.wal import WriteAheadLog
from embeddeddb.transactional_page_writer import TransactionalPageWriter
from embeddeddb.transaction import Transaction
from embeddeddb.recovery import RecoveryManager


def test_recovers_uncommitted_page_after_reopening(tmp_path):
    db_path = tmp_path / "test.db"
    wal_path = tmp_path / "test.wal"

    # 1. Create and initialize the database.
    initial_manager = PageManager(db_path)
    initial_manager.initialize()
    initial_manager.open()

    page_id = initial_manager.allocate_page()

    original_page = b"A" * PAGE_SIZE
    initial_manager.write_page(page_id, original_page)
    initial_manager.sync()

    # 2. Open the WAL and start a transaction.
    wal = WriteAheadLog(wal_path)
    wal.open()

    writer = TransactionalPageWriter(initial_manager, wal)
    transaction = Transaction(1, wal, writer)

    # 3. Modify the page but deliberately do not commit.
    modified_page = b"B" * PAGE_SIZE
    transaction.write_page(page_id, modified_page)

    # 4. Simulate a crash: close files without rollback.
    initial_manager.close()
    wal.close()

    # 5. Reopen the database and WAL.
    recovered_manager = PageManager(db_path)
    recovered_manager.open()

    recovered_wal = WriteAheadLog(wal_path)
    recovered_wal.open()

    recovered_writer = TransactionalPageWriter(
        recovered_manager,
        recovered_wal,
    )

    # 6. Run recovery.
    recovery = RecoveryManager(recovered_wal)

    # First recovery.
    first_result = recovery.recover(recovered_writer)

    assert recovered_manager.read_page(page_id) == original_page
    assert first_result["recovered_transactions"] == [1]
    assert first_result["restored_pages"] == 1

    # Second recovery on the same database and WAL.
    second_result = recovery.recover(recovered_writer)

    assert recovered_manager.read_page(page_id) == original_page
    assert second_result["recovered_transactions"] == [1]
    assert second_result["restored_pages"] == 1

    recovered_manager.close()
    recovered_wal.close()