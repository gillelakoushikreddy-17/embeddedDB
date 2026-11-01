
from embeddeddb.page_manager import PageManager, PAGE_SIZE
import pytest
from embeddeddb.header import create_header

def test_write_and_read_page(tmp_path):
    database_file = tmp_path / "test.db"

    manager = PageManager(database_file)
    manager.initialize()
    manager.open()
    try:
        manager.open()

        page_data = b"Hello" + b"\x00" * (PAGE_SIZE - 5)

        manager.write_page(1, page_data)
        result = manager.read_page(1)

        assert result == page_data
        assert len(result) == PAGE_SIZE

    finally:
        manager.close()





def test_write_page_rejects_wrong_size(tmp_path):
    database_file = tmp_path / "test.db"

    manager = PageManager(database_file)
    manager.initialize()

    manager = PageManager(database_file)

    try:
        manager.open()

        with pytest.raises(ValueError, match="exactly 4096 bytes"):
            manager.write_page(1, b"Hello")

    finally:
        manager.close()

def test_negative_page_id_is_rejected(tmp_path):
    database_file = tmp_path / "test.db"

    manager = PageManager(database_file)
    manager.initialize()

    manager = PageManager(database_file)

    try:
        manager.open()

        with pytest.raises(ValueError, match="non-negative integer"):
            manager.read_page(-1)

    finally:
        manager.close()


def test_initialize_creates_header_page(tmp_path):
    database_file = tmp_path / "new.db"

    manager = PageManager(database_file)
    manager.initialize()

    data = database_file.read_bytes()

    assert len(data) == PAGE_SIZE
    assert data[:20] == create_header()

def test_initialize_does_not_overwrite_existing_file(tmp_path):
    database_file = tmp_path / "existing.db"
    database_file.write_bytes(b"original data")

    manager = PageManager(database_file)

    with pytest.raises(FileExistsError):
        manager.initialize()

    assert database_file.read_bytes() == b"original data"

def test_open_rejects_invalid_database_file(tmp_path):
    database_file = tmp_path / "invalid.db"
    database_file.write_bytes(b"not a valid database")

    manager = PageManager(database_file)

    with pytest.raises(ValueError):
        manager.open()

    assert manager.file is None

def test_open_rejects_incomplete_database_file(tmp_path):
    database_file = tmp_path / "incomplete.db"

    # Create a valid header, but write only 16 bytes.
    database_file.write_bytes(create_header())

    manager = PageManager(database_file)

    try:
        with pytest.raises(ValueError):
            manager.open()
    finally:
        manager.close()

def test_open_rejects_file_with_partial_page(tmp_path):
    database_file = tmp_path / "partial.db"

    manager = PageManager(database_file)
    manager.initialize()
    manager.close()

    # Append only 4 bytes of what would be page 1.
    with open(database_file, "ab") as file:
        file.write(b"DATA")

    manager = PageManager(database_file)

    try:
        with pytest.raises(ValueError):
            manager.open()
    finally:
        manager.close()


from embeddeddb.header import read_header


def test_update_root_page_id(tmp_path):
    db_path = tmp_path / "root.db"

    manager = PageManager(db_path)
    manager.initialize()
    manager.open()

    try:
        manager.update_root_page_id(1)

        page_zero = manager.read_page(0)
        header = read_header(page_zero)

        assert header["root_page_id"] == 1

    finally:
        manager.close()


