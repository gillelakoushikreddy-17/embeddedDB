import tempfile
from pathlib import Path

from embeddeddb.database import EmbeddedDB

with tempfile.TemporaryDirectory() as temp_dir:
    db_path = Path(temp_dir) / "my_database"

    db = EmbeddedDB(db_path)
    db.open()

    db.put(10, b"Hello")
    db.put(20, b"World")

    # Test keys that don't exist
    assert db.get(30) is None
    assert db.get(0) is None
    assert db.get(-1) is None

    # Existing keys must still work
    assert db.get(10) == b"Hello"
    assert db.get(20) == b"World"

    db.close()

print("Missing-key test passed!")