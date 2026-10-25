from objectdb.record import encode_record, decode_record, RECORD_SIZE


def test_encode_and_decode_record():
    encoded = encode_record(1, 8192, 250000)

    assert len(encoded) == RECORD_SIZE

    decoded = decode_record(encoded)

    assert decoded == (1, 8192, 250000)