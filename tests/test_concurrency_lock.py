import multiprocessing
import pytest
from objectdb import ObjectDB


def _worker_open_db(db_path, queue):
    """Attempt to open an ObjectDB instance in a separate process."""
    try:
        db = ObjectDB(db_path)
        db.open()
        queue.put("SUCCESS")
        import time
        time.sleep(0.5)
        db.close()
    except Exception as exc:
        queue.put(f"ERROR: {exc}")


def test_concurrent_open_in_same_process_raises_error(tmp_path):
    """Opening the same database directory twice in one process raises RuntimeError."""
    db_path = tmp_path / "test_db"
    db1 = ObjectDB(db_path)
    db1.open()

    db2 = ObjectDB(db_path)
    with pytest.raises(RuntimeError, match="already open"):
        db2.open()

    db1.close()

    # Once db1 is closed, db2 can open successfully
    db2.open()
    db2.close()


def test_concurrent_open_in_separate_process_raises_error(tmp_path):
    """A second process attempting to open an open database raises RuntimeError."""
    db_path = tmp_path / "test_db"
    db = ObjectDB(db_path)
    db.open()

    queue = multiprocessing.Queue()
    proc = multiprocessing.Process(target=_worker_open_db, args=(db_path, queue))
    proc.start()
    proc.join(timeout=3)

    result = queue.get(timeout=2)
    assert "ERROR" in result
    assert "locked by another process" in result

    db.close()
