from embeddeddb.page_manager import PageManager


def test_allocate_page(tmp_path):
    db_path = tmp_path / "test.db"

    manager = PageManager(db_path)
    manager.initialize()
    manager.open()

    try:
        page_id = manager.allocate_page()

        assert page_id == 1
        assert len(manager.read_page(page_id)) == 4096
        assert manager.read_page(page_id) == b"\x00" * 4096
    finally:
        manager.close()

def test_allocate_multiple_pages(tmp_path):
    db_path = tmp_path / "test_multiple.db"

    manager = PageManager(db_path)
    manager.initialize()
    manager.open()

    try:
        first_page = manager.allocate_page()
        second_page = manager.allocate_page()
        third_page = manager.allocate_page()

        assert first_page == 1
        assert second_page == 2
        assert third_page == 3

        assert manager.read_page(1) == b"\x00" * 4096
        assert manager.read_page(2) == b"\x00" * 4096
        assert manager.read_page(3) == b"\x00" * 4096
    finally:
        manager.close()