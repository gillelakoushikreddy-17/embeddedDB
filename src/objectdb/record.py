import struct

RECORD_FORMAT = "<IQQ"
RECORD_SIZE = struct.calcsize(RECORD_FORMAT)

def encode_record(segment_id, offset, size):
    if not isinstance(segment_id, int) or isinstance(segment_id, bool) or segment_id < 0 or segment_id > 0xFFFFFFFF:
        raise ValueError("segment_id must be a 32-bit unsigned integer")
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0 or offset > 0xFFFFFFFFFFFFFFFF:
        raise ValueError("offset must be a 64-bit unsigned integer")
    if not isinstance(size, int) or isinstance(size, bool) or size < 0 or size > 0xFFFFFFFFFFFFFFFF:
        raise ValueError("size must be a 64-bit unsigned integer")
    return struct.pack(
        RECORD_FORMAT,
        segment_id,
        offset,
        size,
    )

def decode_record(data):
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError("Record data must be bytes-like")
    if len(data) != RECORD_SIZE:
        raise ValueError(f"Invalid record size: expected {RECORD_SIZE} bytes, got {len(data)}")
    return struct.unpack(RECORD_FORMAT, data)

