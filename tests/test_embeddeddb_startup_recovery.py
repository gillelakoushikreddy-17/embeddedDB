from embeddeddb.database import EmbeddedDB
from embeddeddb.page_manager import PAGE_SIZE, PageManager
from embeddeddb.wal import WriteAheadLog
from embeddeddb.transaction import Transaction
from embeddeddb.transactional_page_writer import TransactionalPageWriter


def test_objectdb_open_recovers_incomplete_transaction(tmp_path):
    db_path = tmp_path / "my_database"

    # 1. Create and initialize the database normally.
    db = EmbeddedDB(db_path)
    db.open()

    manager = db.page_manager
    wal = db.wal

    # 2. Allocate a page and store its original contents.
    page_id = manager.allocate_page()
    original_page = b"A" * PAGE_SIZE
    manager.write_page(page_id, original_page)
    manager.sync()

    # 3. Start a transaction and modify the page.
    writer = TransactionalPageWriter(manager, wal)
    transaction = Transaction(1, wal, writer)

    transaction.write_page(page_id, b"B" * PAGE_SIZE)

    # 4. Simulate an interrupted transaction by closing files
    # without committing or rolling back.
    db.segment.close()
    manager.close()
    wal.close()
    db.is_open = False

    # 5. Open a fresh EmbeddedDB instance.
    recovered_db = EmbeddedDB(db_path)
    recovered_db.open()

    # 6. Verify startup recovery restored the original contents.
    assert recovered_db.page_manager.read_page(page_id) == original_page

    recovered_db.close()