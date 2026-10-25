import pytest
from objectdb.btree import (
    encode_internal_node,
    decode_internal_node,
    PAGE_SIZE,
)
from objectdb.page_manager import PageManager


def test_internal_node_round_trip():
    keys = [30, 60]
    child_page_ids = [2, 3, 4]

    page = encode_internal_node(keys, child_page_ids)

    assert len(page) == PAGE_SIZE

    decoded_keys, decoded_children = decode_internal_node(page)

    assert decoded_keys == keys
    assert decoded_children == child_page_ids



def test_reject_wrong_page_size():
    with pytest.raises(ValueError, match="exactly 4096 bytes"):
        decode_internal_node(b"\x00" * 100)


def test_reject_wrong_node_type():
    page = bytearray(PAGE_SIZE)
    page[0] = 99  # Unknown node type

    with pytest.raises(ValueError, match="not an internal node"):
        decode_internal_node(bytes(page))

def test_reject_wrong_page_size():
    with pytest.raises(ValueError, match="exactly 4096 bytes"):
        decode_internal_node(b"\x00" * 100)


def test_reject_wrong_node_type():
    page = bytearray(PAGE_SIZE)
    page[0] = 99  # Unknown node type

    with pytest.raises(ValueError, match="not an internal node"):
        decode_internal_node(bytes(page))

def test_write_internal_node_to_disk(tmp_path):
    db_path = tmp_path / "btree.db"

    manager = PageManager(db_path)
    manager.initialize()
    manager.open()

    try:
        keys = [30, 60]
        child_page_ids = [2, 3, 4]

        page_data = encode_internal_node(keys, child_page_ids)
        page_id = manager.allocate_page()

        manager.write_page(page_id, page_data)

        stored_page = manager.read_page(page_id)
        decoded_keys, decoded_children = decode_internal_node(stored_page)

        assert page_id == 1
        assert decoded_keys == keys
        assert decoded_children == child_page_ids
    finally:
        manager.close()