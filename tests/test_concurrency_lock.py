import multiprocessing
import os
import sys
import pytest
from embeddeddb import EmbeddedDB

mp_ctx = multiprocessing.get_context("spawn")


def _worker_try_open(db_path_str, result_queue):
    """Attempt to open an EmbeddedDB instance in a separate spawned process."""
    try:
        db = EmbeddedDB(db_path_str)
        db.open()
        result_queue.put("SUCCESS")
        db.close()
    except Exception as exc:
        result_queue.put(f"ERROR: {exc}")


def _worker_hold_lock(db_path_str, started_event, release_event, result_queue):
    """Open database, signal started_event, wait for release_event, then close."""
    try:
        db = EmbeddedDB(db_path_str)
        db.open()
        started_event.set()
        if not release_event.wait(timeout=10):
            result_queue.put("ERROR: release_event timeout")
            return
        db.close()
        result_queue.put("SUCCESS_CLOSED")
    except Exception as exc:
        result_queue.put(f"ERROR: {exc}")


def _worker_open_and_hold(db_path_str, started_event):
    """Open database, signal started_event, and loop until terminated."""
    db = EmbeddedDB(db_path_str)
    db.open()
    started_event.set()
    import time
    while True:
        time.sleep(0.05)


def test_concurrent_open_in_same_process_raises_error(tmp_path):
    """Opening the same database directory twice in one process raises RuntimeError."""
    db_path = tmp_path / "test_db_same_proc"
    db1 = EmbeddedDB(db_path)
    db1.open()

    db2 = EmbeddedDB(db_path)
    with pytest.raises(RuntimeError, match="already open by another instance in this process"):
        db2.open()

    db1.close()

    # Once db1 is closed, db2 can open successfully
    db2.open()
    db2.close()


def test_second_spawned_process_rejected_while_first_holds_lock(tmp_path):
    """
    A spawned process (with empty in-process registry) attempting to open an already-locked
    database is rejected by OS-level file locking.
    """
    db_path = tmp_path / "test_db_cross_proc"
    db1 = EmbeddedDB(db_path)
    db1.open()

    queue = mp_ctx.Queue()
    proc = mp_ctx.Process(target=_worker_try_open, args=(str(db_path), queue))
    try:
        proc.start()
        proc.join(timeout=5)

        result = queue.get(timeout=2)
        assert "ERROR" in result
        assert "locked by another process" in result
    finally:
        if proc.is_alive():
            proc.kill()
            proc.join(timeout=2)
        queue.close()
        queue.join_thread()
        db1.close()


def test_spawned_process_succeeds_after_first_closes(tmp_path):
    """A spawned process can open the database once the first process closes it."""
    db_path = tmp_path / "test_db_handover"
    started_event = mp_ctx.Event()
    release_event = mp_ctx.Event()
    result_queue = mp_ctx.Queue()

    # Process 1 opens and holds the lock
    proc1 = mp_ctx.Process(
        target=_worker_hold_lock,
        args=(str(db_path), started_event, release_event, result_queue),
    )
    proc2 = None
    proc3 = None
    q2 = None
    q3 = None
    try:
        proc1.start()
        assert started_event.wait(timeout=5), "Process 1 failed to open database in time"

        # Process 2 attempts to open while Process 1 holds the lock -> rejected
        q2 = mp_ctx.Queue()
        proc2 = mp_ctx.Process(target=_worker_try_open, args=(str(db_path), q2))
        proc2.start()
        proc2.join(timeout=5)
        r2 = q2.get(timeout=2)
        assert "ERROR" in r2
        assert "locked by another process" in r2

        # Signal Process 1 to close
        release_event.set()
        proc1.join(timeout=5)
        assert result_queue.get(timeout=2) == "SUCCESS_CLOSED"

        # Process 3 now attempts to open -> succeeds
        q3 = mp_ctx.Queue()
        proc3 = mp_ctx.Process(target=_worker_try_open, args=(str(db_path), q3))
        proc3.start()
        proc3.join(timeout=5)
        r3 = q3.get(timeout=2)
        assert r3 == "SUCCESS"
    finally:
        release_event.set()
        for p in (proc1, proc2, proc3):
            if p is not None and p.is_alive():
                p.kill()
                p.join(timeout=2)
        for q in (result_queue, q2, q3):
            if q is not None:
                q.close()
                q.join_thread()


def test_lock_released_when_owning_process_terminates(tmp_path):
    """When a process holding the lock terminates abruptly, the OS releases the lock."""
    db_path = tmp_path / "test_db_terminated_proc"
    started_event = mp_ctx.Event()

    proc = mp_ctx.Process(
        target=_worker_open_and_hold,
        args=(str(db_path), started_event),
    )
    try:
        proc.start()
        assert started_event.wait(timeout=5), "Worker failed to start and open DB"

        # Abruptly terminate the process
        proc.terminate()
        proc.join(timeout=3)
    finally:
        if proc.is_alive():
            proc.kill()
            proc.join(timeout=2)

    # Parent process can now open the database
    db = EmbeddedDB(db_path)
    db.open()
    assert db.is_open is True
    db.close()


def test_initialization_failure_does_not_leave_stale_lock(tmp_path):
    """If database opening fails during initialization, no stale lock is left for other processes."""
    from unittest.mock import patch
    from embeddeddb.recovery import RecoveryManager

    db_path = tmp_path / "test_db_init_fail_lock"
    db = EmbeddedDB(db_path)

    with patch.object(RecoveryManager, "recover", side_effect=RuntimeError("Recovery failed")):
        with pytest.raises(RuntimeError, match="Recovery failed"):
            db.open()

    # Now verify a spawned separate process can open the database without lock conflict
    queue = mp_ctx.Queue()
    proc = mp_ctx.Process(target=_worker_try_open, args=(str(db_path), queue))
    try:
        proc.start()
        proc.join(timeout=5)

        result = queue.get(timeout=2)
        assert result == "SUCCESS", f"Expected SUCCESS but got: {result}"
    finally:
        if proc.is_alive():
            proc.kill()
            proc.join(timeout=2)
        queue.close()
        queue.join_thread()
