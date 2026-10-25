import struct

RECORD_FORMAT = "<IQQ"
RECORD_SIZE = struct.calcsize(RECORD_FORMAT)

def encode_record(segment_id, offset, size):
    return struct.pack(
        RECORD_FORMAT,
        segment_id,
        offset,
        size,
    )

def decode_record(data):
    return struct.unpack(RECORD_FORMAT, data)

