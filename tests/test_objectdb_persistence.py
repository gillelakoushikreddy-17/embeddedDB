from objectdb.database import ObjectDB


def test_data_persists_after_close_and_reopen(tmp_path):
    db_path = tmp_path / "test_db"

    # Open the database and store data.
    db = ObjectDB(db_path)
    db.open()
    db.put(1, b"Hello, ObjectDB!")
    db.close()

    # Reopen the same database and verify persistence.
    db = ObjectDB(db_path)
    db.open()

    try:
        assert db.get(1) == b"Hello, ObjectDB!"
    finally:
        db.close()


from objectdb.database import ObjectDB


def test_multiple_records_persist_after_reopen(tmp_path):
    db_path = tmp_path / "test_db"

    db = ObjectDB(db_path)
    db.open()

    db.put(1, b"Hello")
    db.put(2, b"ObjectDB")
    db.put(3, b"Database Engine")

    db.close()

    db = ObjectDB(db_path)
    db.open()

    try:
        assert db.get(1) == b"Hello"
        assert db.get(2) == b"ObjectDB"
        assert db.get(3) == b"Database Engine"
    finally:
        db.close()


def test_updated_value_persists_after_reopen(tmp_path):
    db_path = tmp_path / "test_db"

    db = ObjectDB(db_path)
    db.open()

    db.put(1, b"Old Value")
    db.put(1, b"New Value")

    assert db.get(1) == b"New Value"

    db.close()

    db = ObjectDB(db_path)
    db.open()

    try:
        assert db.get(1) == b"New Value"
    finally:
        db.close()
