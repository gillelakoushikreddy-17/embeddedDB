
import json

import pytest

from objectdb.wal import WriteAheadLog


def test_append_and_read_records(tmp_path):
    wal = WriteAheadLog(tmp_path / "test.wal")

    try:
        wal.open()

        first_lsn = wal.append(
            "INSERT",
            {"key": 10, "value": "hello"},
        )

        second_lsn = wal.append(
            "UPDATE",
            {"key": 10, "value": "world"},
        )

        records = wal.read_all()

        assert first_lsn == 1
        assert second_lsn == 2
        assert len(records) == 2

        assert records[0]["type"] == "INSERT"
        assert records[0]["data"]["value"] == "hello"

        assert records[1]["type"] == "UPDATE"
        assert records[1]["data"]["value"] == "world"

    finally:
        wal.close()


def test_lsn_continues_after_reopening(tmp_path):
    path = tmp_path / "reopen.wal"

    wal = WriteAheadLog(path)
    wal.open()

    assert wal.append("INSERT", {"key": 1}) == 1
    wal.close()

    wal = WriteAheadLog(path)

    try:
        wal.open()

        assert wal.append("INSERT", {"key": 2}) == 2

    finally:
        wal.close()


def test_append_requires_open_wal(tmp_path):
    wal = WriteAheadLog(tmp_path / "closed.wal")

    with pytest.raises(RuntimeError):
        wal.append("INSERT", {"key": 1})


def test_empty_record_type_is_rejected(tmp_path):
    wal = WriteAheadLog(tmp_path / "invalid.wal")

    try:
        wal.open()

        with pytest.raises(ValueError):
            wal.append("", {"key": 1})

    finally:
        wal.close()


def test_non_json_serializable_data_is_rejected(tmp_path):
    wal = WriteAheadLog(tmp_path / "invalid_data.wal")

    try:
        wal.open()

        with pytest.raises(ValueError):
            wal.append("INSERT", {"value": object()})

    finally:
        wal.close()
def test_open_discards_truncated_final_record(tmp_path):
    wal_path = tmp_path / "test.wal"

    valid_record = {
        "lsn": 1,
        "type": "BEGIN",
        "data": {"transaction_id": 1},
    }

    with open(wal_path, "wb") as file:
        file.write(
            (json.dumps(valid_record) + "\n").encode("utf-8")
        )
        file.write(b'{"lsn":2,"type":"COMMIT","data":')

    wal = WriteAheadLog(wal_path)

    try:
        wal.open()

        records = wal.read_all()

        assert records == [valid_record]
        assert wal.next_lsn == 2
    finally:
        wal.close()


def test_open_rejects_corruption_before_final_record(tmp_path):
    wal_path = tmp_path / "test.wal"

    with open(wal_path, "wb") as file:
        file.write(b"not valid JSON\n")
        file.write(b'{"lsn":2,"type":"COMMIT","data":')

    wal = WriteAheadLog(wal_path)

    with pytest.raises(ValueError, match="invalid record"):
        wal.open()

    assert wal.file is None

@pytest.mark.parametrize(
    "invalid_record",
    [
        b"[]\n",
        b"null\n",
        b'"hello"\n',
        b"123\n",
    ],
)
def test_open_rejects_non_dictionary_records(
    tmp_path, invalid_record
):
    wal_path = tmp_path / "invalid_record.wal"
    wal_path.write_bytes(invalid_record)

    wal = WriteAheadLog(wal_path)

    try:
        with pytest.raises(ValueError, match="invalid record"):
            wal.open()
    finally:
        wal.close()


@pytest.mark.parametrize(
    "record",
    [
        # Missing required fields
        {"lsn": 1},
        {"type": "BEGIN"},
        {"data": {"transaction_id": 1}},

        # Invalid field types
        {"lsn": 1, "type": 123, "data": {}},
        {"lsn": 1, "type": "", "data": {}},
        {"lsn": 1, "type": "BEGIN", "data": []},
        {"lsn": 1, "type": "BEGIN", "data": None},
    ],
)
def test_open_rejects_invalid_record_structure(tmp_path, record):
    import json

    wal_path = tmp_path / "invalid_structure.wal"
    wal_path.write_bytes(
        (json.dumps(record) + "\n").encode("utf-8")
    )

    wal = WriteAheadLog(wal_path)

    try:
        with pytest.raises(ValueError):
            wal.open()
    finally:
        wal.close()

