
import pytest

from objectdb.page_manager import PageManager, PAGE_SIZE
from objectdb.wal import WriteAheadLog
from objectdb.transaction import Transaction, TransactionState
from objectdb.transactional_page_writer import TransactionalPageWriter


def setup_transaction(tmp_path, name="transaction"):
    db_path = tmp_path / f"{name}.db"
    wal_path = tmp_path / f"{name}.wal"

    manager = PageManager(db_path)
    manager.initialize()
    manager.open()

    page_id = manager.allocate_page()

    wal = WriteAheadLog(wal_path)
    wal.open()

    writer = TransactionalPageWriter(manager, wal)
    tx = Transaction(1, wal, writer)

    return manager, wal, writer, tx, page_id


def close_resources(manager, wal):
    wal.close()
    manager.close()


def test_transaction_begins(tmp_path):
    manager, wal, _, tx, _ = setup_transaction(tmp_path, "begin")

    try:
        assert tx.state == TransactionState.ACTIVE

        records = wal.read_all()

        assert records[0]["type"] == "BEGIN"
        assert records[0]["data"]["transaction_id"] == 1

    finally:
        close_resources(manager, wal)


def test_transaction_commits(tmp_path):
    manager, wal, _, tx, _ = setup_transaction(tmp_path, "commit")

    try:
        tx.commit()

        assert tx.state == TransactionState.COMMITTED

        records = wal.read_all()

        assert [r["type"] for r in records] == ["BEGIN", "COMMIT"]

    finally:
        close_resources(manager, wal)


def test_transaction_rolls_back(tmp_path):
    manager, wal, _, tx, page_id = setup_transaction(
        tmp_path, "rollback"
    )

    try:
        original = b"A" * PAGE_SIZE
        updated = b"B" * PAGE_SIZE

        manager.write_page(page_id, original)
        tx.write_page(page_id, updated)

        tx.rollback()

        assert manager.read_page(page_id) == original
        assert tx.state == TransactionState.ABORTED

        records = wal.read_all()

        assert records[-1]["type"] == "ROLLBACK"

    finally:
        close_resources(manager, wal)


def test_rollback_restores_multiple_pages(tmp_path):
    manager, wal, _, tx, first_page_id = setup_transaction(
        tmp_path, "multiple"
    )

    try:
        second_page_id = manager.allocate_page()

        first_original = b"A" * PAGE_SIZE
        second_original = b"C" * PAGE_SIZE

        manager.write_page(first_page_id, first_original)
        manager.write_page(second_page_id, second_original)

        tx.write_page(first_page_id, b"B" * PAGE_SIZE)
        tx.write_page(second_page_id, b"D" * PAGE_SIZE)

        tx.rollback()

        assert manager.read_page(first_page_id) == first_original
        assert manager.read_page(second_page_id) == second_original

    finally:
        close_resources(manager, wal)


def test_rollback_restores_repeated_writes_in_reverse_order(tmp_path):
    manager, wal, _, tx, page_id = setup_transaction(
        tmp_path, "repeated"
    )

    try:
        original = b"A" * PAGE_SIZE

        manager.write_page(page_id, original)

        tx.write_page(page_id, b"B" * PAGE_SIZE)
        tx.write_page(page_id, b"C" * PAGE_SIZE)

        tx.rollback()

        assert manager.read_page(page_id) == original

    finally:
        close_resources(manager, wal)


def test_cannot_commit_twice(tmp_path):
    manager, wal, _, tx, _ = setup_transaction(
        tmp_path, "double_commit"
    )

    try:
        tx.commit()

        with pytest.raises(RuntimeError):
            tx.commit()

    finally:
        close_resources(manager, wal)


def test_cannot_rollback_committed_transaction(tmp_path):
    manager, wal, _, tx, _ = setup_transaction(
        tmp_path, "invalid_rollback"
    )

    try:
        tx.commit()

        with pytest.raises(RuntimeError):
            tx.rollback()

    finally:
        close_resources(manager, wal)


def test_invalid_transaction_id_is_rejected(tmp_path):
    manager, wal, writer, _, _ = setup_transaction(
        tmp_path, "invalid_id"
    )

    try:
        with pytest.raises(ValueError):
            Transaction(0, wal, writer)

    finally:
        close_resources(manager, wal)
