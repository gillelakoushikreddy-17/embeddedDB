from objectdb.segment import Segment
import pytest
def test_segment_read_after_append(tmp_path):
    segment = Segment(tmp_path / "segment_0.dat")
    segment.open()

    try:
        offset, size = segment.append(b"Hello")
        offset2, size2 = segment.append(b"World")

        assert segment.read(offset, size) == b"Hello"
        assert segment.read(offset2, size2) == b"World"
    finally:
        segment.close()



def test_segment_persistence_after_reopen(tmp_path):
    segment_path = tmp_path / "segment_0.dat"

    segment = Segment(segment_path)
    segment.open()

    try:
        offset, size = segment.append(b"Persistent ObjectDB")
    finally:
        segment.close()

    reopened_segment = Segment(segment_path)
    reopened_segment.open()

    try:
        result = reopened_segment.read(offset, size)
        assert result == b"Persistent ObjectDB"
    finally:
        reopened_segment.close()

def test_segment_read_beyond_end_raises_error(tmp_path):
    segment = Segment(tmp_path / "segment_0.dat")
    segment.open()

    try:
        segment.append(b"Hello")

        with pytest.raises(ValueError, match="Not enough data"):
            segment.read(0, 10)
    finally:


        segment.close()
def test_segment_append_rejects_string(tmp_path):
    segment = Segment(tmp_path / "segment_0.dat")
    segment.open()

    try:
        with pytest.raises(TypeError):
            segment.append("Hello")
    finally:
        segment.close()

def test_segment_append_empty_value(tmp_path):
    segment = Segment(tmp_path / "segment_0.dat")
    segment.open()

    try:
        offset, size = segment.append(b"")

        assert offset == 0
        assert size == 0
        assert segment.read(offset, size) == b""
    finally:
        segment.close()


def test_segment_read_zero_size_beyond_end_rejected(tmp_path):
    segment = Segment(tmp_path / "segment_zero.dat")
    segment.open()
    try:
        segment.append(b"data")
        # Reading at offset 10 with size 0 beyond EOF must be rejected
        with pytest.raises(ValueError, match="Not enough data"):
            segment.read(10, 0)
    finally:
        segment.close()


def test_segment_idempotent_open(tmp_path):
    segment = Segment(tmp_path / "segment_idem.dat")
    segment.open()
    fp1 = segment.file
    segment.open()  # Second open should be a no-op
    assert segment.file is fp1
    segment.close()