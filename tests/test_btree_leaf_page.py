from objectdb.btree import (
    encode_leaf_node,
    decode_leaf_node,
    PAGE_SIZE,
)
from objectdb.record import encode_record


def test_leaf_node_round_trip():
    keys = [10, 20]

    records = [
        encode_record(1, 100, 500),
        encode_record(2, 200, 1000),
    ]

    page = encode_leaf_node(keys, records)

    assert len(page) == PAGE_SIZE

    decoded_keys, decoded_records = decode_leaf_node(page)

    assert decoded_keys == keys
    assert decoded_records == records


from objectdb.page_manager import PageManager


def test_write_leaf_node_to_disk(tmp_path):
    db_path = tmp_path / "leaf.db"

    manager = PageManager(db_path)
    manager.initialize()
    manager.open()

    try:
        keys = [10, 20]
        records = [
            encode_record(1, 100, 500),
            encode_record(2, 200, 1000),
        ]

        page_data = encode_leaf_node(keys, records)
        page_id = manager.allocate_page()

        manager.write_page(page_id, page_data)

        stored_page = manager.read_page(page_id)
        decoded_keys, decoded_records = decode_leaf_node(stored_page)

        assert page_id == 1
        assert decoded_keys == keys
        assert decoded_records == records
    finally:
        manager.close()