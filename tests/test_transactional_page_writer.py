
import base64

import pytest

from embeddeddb.page_manager import PageManager, PAGE_SIZE
from embeddeddb.wal import WriteAheadLog
from embeddeddb.transactional_page_writer import TransactionalPageWriter


def setup_database(tmp_path):
    db_path = tmp_path / "test.db"
    wal_path = tmp_path / "test.wal"

    manager = PageManager(db_path)
    manager.initialize()
    manager.open()

    page_id = manager.allocate_page()

    wal = WriteAheadLog(wal_path)
    wal.open()

    writer = TransactionalPageWriter(manager, wal)

    return manager, wal, writer, page_id


def test_write_page_logs_before_image(tmp_path):
    manager, wal, writer, page_id = setup_database(tmp_path)

    try:
        original = b"A" * PAGE_SIZE
        updated = b"B" * PAGE_SIZE

        manager.write_page(page_id, original)
        writer.write_page(1, page_id, updated)

        assert manager.read_page(page_id) == updated

        records = wal.read_all()
        before_image = records[-1]

        assert before_image["type"] == "PAGE_BEFORE_IMAGE"
        assert before_image["data"]["transaction_id"] == 1
        assert before_image["data"]["page_id"] == page_id

        decoded = base64.b64decode(
            before_image["data"]["original_data"]
        )

        assert decoded == original

    finally:
        wal.close()
        manager.close()


def test_restore_page_restores_original_data(tmp_path):
    manager, wal, writer, page_id = setup_database(tmp_path)

    try:
        original = b"A" * PAGE_SIZE
        updated = b"B" * PAGE_SIZE

        manager.write_page(page_id, original)
        writer.write_page(1, page_id, updated)

        records = wal.read_all()
        encoded_original = records[-1]["data"]["original_data"]

        writer.restore_page(page_id, encoded_original)

        assert manager.read_page(page_id) == original

    finally:
        wal.close()
        manager.close()


def test_invalid_page_size_is_rejected(tmp_path):
    manager, wal, writer, page_id = setup_database(tmp_path)

    try:
        with pytest.raises(ValueError):
            writer.write_page(1, page_id, b"too short")

    finally:
        wal.close()
        manager.close()


def test_header_page_cannot_be_written(tmp_path):
    manager, wal, writer, _ = setup_database(tmp_path)

    try:
        with pytest.raises(ValueError):
            writer.write_page(1, 0, b"B" * PAGE_SIZE)

    finally:
        wal.close()
        manager.close()

def test_allocate_page_records_allocation_in_wal(
    tmp_path,
):
    from embeddeddb.page_manager import PageManager
    from embeddeddb.wal import WriteAheadLog
    from embeddeddb.transactional_page_writer import (
        TransactionalPageWriter,
    )

    db_path = tmp_path / "test.db"
    wal_path = tmp_path / "test.wal"

    manager = PageManager(db_path)
    manager.initialize()
    manager.open()

    wal = WriteAheadLog(wal_path)
    wal.open()

    try:
        page_writer = TransactionalPageWriter(manager, wal)

        page_id = manager.allocate_page()

        wal.append(
            "PAGE_ALLOCATED",
            {
                "transaction_id": 1,
                "page_id": page_id,
            },
        )

        records = wal.read_all()

        assert records[-1]["type"] == "PAGE_ALLOCATED"
        assert records[-1]["data"]["transaction_id"] == 1
        assert records[-1]["data"]["page_id"] == page_id

    finally:
        wal.close()
        manager.close()

def test_allocate_page_records_allocation_in_wal(tmp_path):
    from embeddeddb.page_manager import PageManager
    from embeddeddb.wal import WriteAheadLog
    from embeddeddb.transactional_page_writer import (
        TransactionalPageWriter,
    )

    db_path = tmp_path / "test.db"
    wal_path = tmp_path / "test.wal"

    manager = PageManager(db_path)
    manager.initialize()
    manager.open()

    wal = WriteAheadLog(wal_path)
    wal.open()

    try:
        page_writer = TransactionalPageWriter(manager, wal)

        page_id = page_writer.allocate_page(transaction_id=1)

        assert page_id == 1

        records = wal.read_all()

        assert records[-1]["type"] == "PAGE_ALLOCATED"
        assert records[-1]["data"]["transaction_id"] == 1
        assert records[-1]["data"]["page_id"] == page_id

    finally:
        wal.close()
        manager.close()
import pytest


@pytest.mark.parametrize("transaction_id", [0, -1, True, 1.5, "1"])
def test_allocate_page_rejects_invalid_transaction_id(
    tmp_path,
    transaction_id,
):
    from embeddeddb.page_manager import PageManager
    from embeddeddb.wal import WriteAheadLog
    from embeddeddb.transactional_page_writer import (
        TransactionalPageWriter,
    )

    db_path = tmp_path / "test.db"
    wal_path = tmp_path / "test.wal"

    manager = PageManager(db_path)
    manager.initialize()
    manager.open()

    wal = WriteAheadLog(wal_path)
    wal.open()

    try:
        page_writer = TransactionalPageWriter(manager, wal)

        with pytest.raises(ValueError, match="Transaction ID"):
            page_writer.allocate_page(transaction_id)

    finally:
        wal.close()
        manager.close()


def test_update_root_page_id_logs_before_image(tmp_path):
    manager, wal, writer, _ = setup_database(tmp_path)
    try:
        # Default root page is NO_ROOT_PAGE (0)
        writer.update_root_page_id(1, 5)

        # Header on disk should now be 5
        from embeddeddb.header import read_header
        assert read_header(manager.read_page(0))["root_page_id"] == 5

        # WAL should contain ROOT_PAGE_BEFORE_IMAGE
        records = wal.read_all()
        last = records[-1]
        assert last["type"] == "ROOT_PAGE_BEFORE_IMAGE"
        assert last["data"]["transaction_id"] == 1
        assert last["data"]["original_root_page_id"] == 0
        assert last["data"]["new_root_page_id"] == 5

        # Restoring should revert root page id
        writer.restore_root_page_id(0)
        assert read_header(manager.read_page(0))["root_page_id"] == 0
    finally:
        wal.close()
        manager.close()


@pytest.mark.parametrize("invalid_tx_id", [0, -1, True, "1", 2.5])
def test_update_root_page_id_rejects_invalid_tx_id(tmp_path, invalid_tx_id):
    manager, wal, writer, _ = setup_database(tmp_path)
    try:
        with pytest.raises(ValueError, match="Transaction ID"):
            writer.update_root_page_id(invalid_tx_id, 1)
    finally:
        wal.close()
        manager.close()


@pytest.mark.parametrize("invalid_root_id", [-1, True, "1", 2.5])
def test_update_root_page_id_rejects_invalid_root_id(tmp_path, invalid_root_id):
    manager, wal, writer, _ = setup_database(tmp_path)
    try:
        with pytest.raises(ValueError, match="Root page ID"):
            writer.update_root_page_id(1, invalid_root_id)
    finally:
        wal.close()
        manager.close()