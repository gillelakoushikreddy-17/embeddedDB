from embeddeddb.database import EmbeddedDB


def test_data_persists_after_close_and_reopen(tmp_path):
    db_path = tmp_path / "test_db"

    # Open the database and store data.
    db = EmbeddedDB(db_path)
    db.open()
    db.put(1, b"Hello, EmbeddedDB!")
    db.close()

    # Reopen the same database and verify persistence.
    db = EmbeddedDB(db_path)
    db.open()

    try:
        assert db.get(1) == b"Hello, EmbeddedDB!"
    finally:
        db.close()


from embeddeddb.database import EmbeddedDB


def test_multiple_records_persist_after_reopen(tmp_path):
    db_path = tmp_path / "test_db"

    db = EmbeddedDB(db_path)
    db.open()

    db.put(1, b"Hello")
    db.put(2, b"EmbeddedDB")
    db.put(3, b"Database Engine")

    db.close()

    db = EmbeddedDB(db_path)
    db.open()

    try:
        assert db.get(1) == b"Hello"
        assert db.get(2) == b"EmbeddedDB"
        assert db.get(3) == b"Database Engine"
    finally:
        db.close()


def test_updated_value_persists_after_reopen(tmp_path):
    db_path = tmp_path / "test_db"

    db = EmbeddedDB(db_path)
    db.open()

    db.put(1, b"Old Value")
    db.put(1, b"New Value")

    assert db.get(1) == b"New Value"

    db.close()

    db = EmbeddedDB(db_path)
    db.open()

    try:
        assert db.get(1) == b"New Value"
    finally:
        db.close()
