import pytest

from objectdb.page_manager import PageManager, PAGE_SIZE
from objectdb.wal import WriteAheadLog
from objectdb.transaction import Transaction
from objectdb.transactional_page_writer import TransactionalPageWriter
from objectdb.recovery import RecoveryManager


class InterruptingPageWriter(TransactionalPageWriter):
    """Simulate a crash after restoring the first page."""

    def __init__(self, page_manager, wal):
        super().__init__(page_manager, wal)
        self.restore_count = 0

    def restore_page(self, page_id, encoded_original):
        super().restore_page(page_id, encoded_original)

        self.restore_count += 1

        if self.restore_count == 1:
            raise RuntimeError("Simulated crash during recovery")


def test_recovery_can_resume_after_interruption(tmp_path):
    db_path = tmp_path / "test.db"
    wal_path = tmp_path / "test.wal"

    # 1. Create the database and two pages.
    manager = PageManager(db_path)
    manager.initialize()
    manager.open()

    page_id_1 = manager.allocate_page()
    page_id_2 = manager.allocate_page()

    original_page_1 = b"A" * PAGE_SIZE
    original_page_2 = b"B" * PAGE_SIZE

    manager.write_page(page_id_1, original_page_1)
    manager.write_page(page_id_2, original_page_2)
    manager.sync()

    # 2. Start a transaction and modify both pages.
    wal = WriteAheadLog(wal_path)
    wal.open()

    writer = TransactionalPageWriter(manager, wal)
    transaction = Transaction(1, wal, writer)

    transaction.write_page(page_id_1, b"X" * PAGE_SIZE)
    transaction.write_page(page_id_2, b"Y" * PAGE_SIZE)

    # 3. Simulate the application crashing.
    manager.close()
    wal.close()

    # 4. Reopen the database and WAL.
    manager = PageManager(db_path)
    manager.open()

    wal = WriteAheadLog(wal_path)
    wal.open()

    # 5. Interrupt the first recovery attempt.
    interrupting_writer = InterruptingPageWriter(manager, wal)
    recovery = RecoveryManager(wal)

    with pytest.raises(RuntimeError, match="Simulated crash"):
        recovery.recover(interrupting_writer)

    # 6. Simulate restarting after the interruption.
    manager.close()
    wal.close()

    manager = PageManager(db_path)
    manager.open()

    wal = WriteAheadLog(wal_path)
    wal.open()

    writer = TransactionalPageWriter(manager, wal)
    recovery = RecoveryManager(wal)

    # 7. Run recovery again.
    result = recovery.recover(writer)

    # 8. Both pages must contain their original contents.
    assert manager.read_page(page_id_1) == original_page_1
    assert manager.read_page(page_id_2) == original_page_2

    assert result["recovered_transactions"] == [1]
    assert result["restored_pages"] == 2

    manager.close()
    wal.close()