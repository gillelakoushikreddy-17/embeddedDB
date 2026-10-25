
from objectdb.header import (
    create_header,
    read_header,
    HEADER_SIZE,
)


def test_header_creation_and_reading():
    data = create_header()

    assert len(data) == HEADER_SIZE

    header = read_header(data)

    assert header["version"] == 1
    assert header["page_size"] == 4096


def test_incomplete_header_is_rejected():
    try:
        read_header(b"HELLO")
    except ValueError:
        pass
    else:
        raise AssertionError("Incomplete header was accepted")
