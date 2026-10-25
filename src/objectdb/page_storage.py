import struct
from objectdb.record import encode_record, decode_record

PAGE_SIZE = 4096

PAGE_HEADER_FORMAT = "<HHH"
PAGE_HEADER_SIZE = struct.calcsize(PAGE_HEADER_FORMAT)

SLOT_FORMAT = "<HH"
SLOT_SIZE = struct.calcsize(SLOT_FORMAT)

def create_page():
    page = bytearray(PAGE_SIZE)

    struct.pack_into(
        PAGE_HEADER_FORMAT,
        page,
        0,
        0,
        PAGE_HEADER_SIZE,
        PAGE_SIZE,
    )

    return page

def read_page_header(page):
    return struct.unpack_from(
        PAGE_HEADER_FORMAT,
        page,
        0,
    )

def insert_record(page, record_data):
    record_count, free_start, free_end = read_page_header(page)

    slot_position = PAGE_HEADER_SIZE + record_count * SLOT_SIZE
    record_length = len(record_data)
    record_offset = free_end - record_length

    if record_offset < slot_position + SLOT_SIZE:
        raise ValueError("Not enough space in page")

    page[record_offset:free_end] = record_data

    struct.pack_into(
        SLOT_FORMAT,
        page,
        slot_position,
        record_offset,
        record_length,
    )

    struct.pack_into(
        PAGE_HEADER_FORMAT,
        page,
        0,
        record_count + 1,
        slot_position + SLOT_SIZE,
        record_offset,
    )

    return record_count

def read_record(page, slot_id):
    record_count, _, _ = read_page_header(page)

    if slot_id < 0 or slot_id >= record_count:
        raise ValueError("Invalid slot ID")

    slot_position = PAGE_HEADER_SIZE + slot_id * SLOT_SIZE

    record_offset, record_length = struct.unpack_from(
        SLOT_FORMAT,
        page,
        slot_position,
    )

    return bytes(page[record_offset:record_offset + record_length])


