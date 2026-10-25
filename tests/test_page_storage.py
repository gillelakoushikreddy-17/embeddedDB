from objectdb.page_storage import (
    create_page,
    insert_record,
    read_record,
    read_page_header,
)


def test_insert_and_read_record():
    page = create_page()

    slot_id = insert_record(page, b"hello")

    result = read_record(page, slot_id)

    assert slot_id == 0
    assert result == b"hello"


def test_multiple_records():
    page = create_page()

    slot_0 = insert_record(page, b"first")
    slot_1 = insert_record(page, b"second")

    assert read_record(page, slot_0) == b"first"
    assert read_record(page, slot_1) == b"second"

    record_count, _, _ = read_page_header(page)
    assert record_count == 2

from objectdb.record import encode_record, decode_record


def test_store_metadata_record():
    page = create_page()

    metadata = encode_record(
        segment_id=1,
        offset=8192,
        size=250000,
    )

    slot_id = insert_record(page, metadata)

    stored_metadata = read_record(page, slot_id)

    decoded = decode_record(stored_metadata)

    assert decoded == (1, 8192, 250000)