# EmbeddedDB

**EmbeddedDB** is a lightweight, reliable, single-process embedded key-value database engine written in Python. It is designed to store metadata and record references, serving as the metadata storage engine for custom storage backends such as S3-like object storage services.

---

## Architecture Overview

EmbeddedDB separates index structure from payload storage:

- **B+ Tree Index (`PersistentBTree` & `Tree`)**: Maintains persistent, ordered indexes of 64-bit integer keys mapped to fixed-size record pointers (`(segment_id, offset, size)`). Supports multiple named trees alongside the default tree, root splits, internal node splits, leaf borrowing, merging, and tree rebalancing.
- **Tree Catalog (`TreeCatalog`)**: Maintains a persistent, crash-safe catalog mapping tree names to root page IDs across chained catalog pages, allowing any number of named trees without arbitrary limits.
- **Page Manager (`PageManager`)**: Allocates, reads, and writes fixed-size 4096-byte database pages (`embeddeddb.data`). Page 0 is reserved for header metadata (including magic number, format version, page size, default root page ID, and catalog root page ID).
- **Segment Storage (`Segment`)**: Append-only log file (`segment-0.dat`) storing arbitrary byte payloads shared across all trees.
- **Write-Ahead Log (`WriteAheadLog`)**: An append-only journal (`embeddeddb.wal`) that records operation lifecycle records (`BEGIN`, `PAGE_ALLOCATED`, `PAGE_BEFORE_IMAGE`, `ROOT_PAGE_BEFORE_IMAGE`, `CATALOG_PAGE_BEFORE_IMAGE`, `COMMIT`, `ROLLBACK`) with monotonically increasing Log Sequence Numbers (LSNs).
- **Transaction Manager (`Transaction` & `TransactionalPageWriter`)**: Ensures ACID properties for database mutations across all trees using before-image undo logging.
- **Crash Recovery (`RecoveryManager`)**: Analyzes the WAL upon startup and restores before-images for any uncommitted/interrupted transactions (including tree creation, catalog updates, splits, and page allocations), ensuring the database is always consistent before accepting operations.
- **Process Concurrency Lock**: Enforces single-process database access using an OS-level file lock (`embeddeddb.lock`), preventing conflicting writers from corrupting data.

---

## Installation

### Prerequisites
- Python 3.10 or higher (tested up to Python 3.13).

### Installing Locally from Wheel
Build or obtain the distribution wheel and install it into your virtual environment:

```bash
pip install path/to/dist/embeddeddb-0.1.0-py3-none-any.whl
```

### Installing in Editable / Development Mode
Clone the repository and install in editable mode:

```bash
git clone <repository-url> embeddeddb
cd embeddeddb

# Standard editable install:
python -m pip install -e .

# Editable install with development and test dependencies (pytest, build):
python -m pip install -e ".[dev]"
```

---

## Quickstart & Basic Usage

### Key & Value Requirements
- **Keys**: Signed 64-bit integers (`int`) in the range \([-2^{63}, 2^{63} - 1]\) (`-9223372036854775808` to `9223372036854775807`).
- **Values**: Arbitrary binary payloads (`bytes`). Structured data (JSON, msgpack, protobuf) must be serialized to `bytes` before storage.

### Context Manager (Recommended)

```python
from embeddeddb import EmbeddedDB

# Open database directory; initializes files on first use
with EmbeddedDB("./my_database") as db:
    # Insert or update records
    db.put(100, b"Hello, EmbeddedDB!")

    # Read records (returns bytes or None if missing)
    value = db.get(100)
    print(value)  # Output: b"Hello, EmbeddedDB!"

    # Query missing keys
    missing = db.get(999)
    print(missing)  # Output: None

    # Delete records (returns True if deleted, False if key did not exist)
    deleted = db.delete(100)
    print(deleted)  # Output: True
    print(db.get(100))  # Output: None

    # Check database structural integrity
    report = db.check_integrity()
    assert report["valid"] is True

    # Create a hot backup in target directory
    db.backup("./my_database_backup")
```

### Manual Open / Close

```python
from embeddeddb import EmbeddedDB

db = EmbeddedDB("./my_database")
db.open()
try:
    db.put(1, b"Payload bytes")
    val = db.get(1)
finally:
    db.close()
```

### Multi-Tree Support (Namespaces)

EmbeddedDB supports multiple independent persistent B+ trees within a single database file. All trees share the underlying page manager, write-ahead log (WAL), and payload segment while maintaining completely isolated keyspaces.

```python
from embeddeddb import EmbeddedDB, TreeAlreadyExistsError, TreeNotFoundError

with EmbeddedDB("./my_database") as db:
    # 1. Create named trees
    users = db.create_tree("users")
    orders = db.create_tree("orders")

    # 2. Key-value isolation across trees
    users.put(1, b'{"name": "Alice"}')
    orders.put(1, b'{"order_id": 1, "item": "Widget"}')

    print(users.get(1))   # b'{"name": "Alice"}'
    print(orders.get(1))  # b'{"order_id": 1, "item": "Widget"}'

    # 3. List all registered trees
    trees = db.list_trees()
    print(trees)  # ['orders', 'users']

    # 4. Open an existing tree
    users_tree = db.open_tree("users")  # alias: db.get_tree("users")
    
    # 5. Get or create a tree
    events = db.get_or_create_tree("events")

    # 6. Delete a key from a tree
    orders.delete(1)
    assert orders.get(1) is None
    assert users.get(1) is not None  # users tree unaffected

    # 7. Drop an entire tree
    dropped = db.drop_tree("orders")
    assert dropped is True
    assert "orders" not in db.list_trees()
```

#### Tree Naming Rules & Exceptions
- **Tree Names**: Must be non-empty strings up to 255 characters, containing only ASCII alphanumeric characters, underscores (`_`), hyphens (`-`), or periods (`.`).
- **`TreeAlreadyExistsError`**: Raised when calling `create_tree(name)` with a name that already exists in the catalog.
- **`TreeNotFoundError`**: Raised when calling `open_tree(name)` or `get_tree(name)` with a name that does not exist in the catalog.
- **Transactions across Multiple Trees**: Named trees participate seamlessly in batch transactions:

```python
with EmbeddedDB("./my_database") as db:
    users = db.get_or_create_tree("users")
    logs = db.get_or_create_tree("audit_logs")

    with db.transaction():
        users.put(42, b'{"name": "Bob"}')
        logs.put(1, b'{"action": "created user 42"}')
        # If an error is raised here, all mutations across both trees roll back atomically!
```

---

## Transactions and Recovery

### Recovery Model: Before-Image (Undo Logging)

EmbeddedDB uses a strict before-image undo logging protocol:

1. **Transaction Start**: Every mutation begins with a `BEGIN` record logged with a unique transaction ID.
2. **Page Allocation**: Before new pages are appended to the database file, `PAGE_ALLOCATED` is logged in the WAL.
3. **Before-Image Logging**: Before any existing page is overwritten, its complete 4096-byte before-image is written to the WAL via `PAGE_BEFORE_IMAGE`.
4. **Metadata Protection**: If a B+ tree split or merge changes the root page ID, `ROOT_PAGE_BEFORE_IMAGE` records the previous root ID before page 0 is updated on disk.
5. **Durability & Commit**: All modified pages and the segment file are flushed to disk (`fsync`), followed by an `fsync`-persisted `COMMIT` record in the WAL.
6. **Rollback**: If an exception occurs or the transaction is aborted, modified pages and root page IDs are restored from before-images in reverse log order, and `ROLLBACK` is recorded.

### Explicit Batch Transactions

Group multiple mutations into a single atomic transaction:

```python
with EmbeddedDB("./my_database") as db:
    with db.transaction():
        db.put(1, b"first")
        db.put(2, b"second")
        # If an exception occurs here, all mutations roll back automatically!
```

### Crash Recovery Guarantees

When opening a database (`db.open()`):
- EmbeddedDB scans the WAL to identify committed, rolled-back, and incomplete transactions.
- Any incomplete transaction (a transaction with a `BEGIN` but neither `COMMIT` nor `ROLLBACK`) is automatically undone by restoring its before-images in reverse WAL order.
- Incomplete tree splits, merges, or page allocations are restored to their consistent pre-transaction state.
- Incomplete or truncated final WAL records (caused by mid-write power loss or process kill) are safely detected and truncated.
- Recovery is idempotent: repeated recoveries produce the identical consistent state.

---

## Integration with S3-Like Object Storage Backends

EmbeddedDB is designed to serve as the **metadata engine** for an S3-like object storage service. String keys (bucket names and object keys) are mapped to 64-bit signed integers, and metadata dictionaries are stored as JSON bytes.

```python
import hashlib
import json
import time
from embeddeddb import EmbeddedDB

class S3MetadataEngine:
    """S3-compatible metadata store using EmbeddedDB."""
    
    def __init__(self, db_path: str):
        self.db = EmbeddedDB(db_path)
        self.db.open()

    def close(self):
        self.db.close()

    def _key_to_id(self, bucket: str, key: str) -> int:
        """Deterministically map bucket + object key string to signed 64-bit int."""
        digest = hashlib.sha256(f"{bucket}/{key}".encode("utf-8")).digest()
        return int.from_bytes(digest[:8], byteorder="big", signed=True)

    def put_object_metadata(self, bucket: str, key: str, metadata: dict):
        """Store object metadata (ETag, size, content-type, timestamps, etc.)."""
        obj_id = self._key_to_id(bucket, key)
        payload = json.dumps(metadata).encode("utf-8")
        self.db.put(obj_id, payload)

    def get_object_metadata(self, bucket: str, key: str) -> dict | None:
        """Retrieve object metadata."""
        obj_id = self._key_to_id(bucket, key)
        raw_bytes = self.db.get(obj_id)
        if raw_bytes is None:
            return None
        return json.loads(raw_bytes.decode("utf-8"))

    def delete_object_metadata(self, bucket: str, key: str) -> bool:
        """Delete object metadata."""
        obj_id = self._key_to_id(bucket, key)
        return self.db.delete(obj_id)

    def atomic_batch_update(self, updates: list[tuple[str, str, dict]]):
        """Update multiple object metadata records atomically."""
        with self.db.transaction():
            for bucket, key, meta in updates:
                self.put_object_metadata(bucket, key, meta)
```

### Recommended Object Storage Architecture
- **EmbeddedDB (Metadata Store)**: Stores bucket names, object keys, ETags, sizes, MIME types, chunk block maps, and access control policies.
- **Blob Storage Layer (Payloads)**: Store large multi-megabyte/gigabyte object payload chunks in dedicated chunk files on disk or raw block storage, keeping the EmbeddedDB metadata index small, fast, and cached in memory.

---

## Concurrency, Durability, and Operating System Semantics

### Single-Process Access & Locking
- EmbeddedDB is designed for **single-process** operation with a single active writer.
- When opening a database, EmbeddedDB acquires an exclusive file lock on `embeddeddb.lock` using platform-native locking (`fcntl.flock(LOCK_EX | LOCK_NB)` on POSIX systems, `msvcrt.locking` on Windows).
- If another process attempts to open the same database directory concurrently, a `RuntimeError` is immediately raised to prevent data corruption.

### Durability Guarantees
- Mutations use Write-Ahead Logging (WAL) and explicit `os.fsync()` calls on data, segment, and journal descriptors before acknowledging commits.
- Interrupted or uncommitted transactions never leak partial changes into the database index.

### Directory Synchronization Semantics
- EmbeddedDB ensures directory metadata durability (such as creating files or installing atomic backups) by synchronizing parent directories via `_sync_directory()`.
- On POSIX platforms (Linux, macOS), directories are opened with `os.O_RDONLY` and synchronized with `os.fsync()`.
- On platforms or filesystem drivers where directory `fsync()` is not supported (such as Windows, CIFS/SMB, or certain network filesystems that raise `EISDIR`, `EINVAL`, `ENOTSUP`, or `EOPNOTSUPP`), EmbeddedDB safely detects unsupported directory synchronization while strictly propagating genuine I/O or permission errors.

---

## Development and Testing

### Running Tests

Run the complete test suite using `pytest`:

```bash
pytest -v
```

Targeted test execution:

```bash
# Run API smoke and integration tests
pytest tests/test_embeddeddb_api.py -v

# Run transaction and crash-recovery suites
pytest tests/test_transaction_integration.py tests/test_crash_recovery_scenarios.py -v

# Run multi-tree index and catalog tests
pytest tests/test_multi_tree.py -v

# Run backup consistency and failure injection tests
pytest tests/test_backup_consistency.py -v
```

### Building Distribution Packages

Build source distribution (`.tar.gz`) and binary wheel (`.whl`):

```bash
# Ensure build tool is installed
python -m pip install --upgrade build

# Clean and build packages
rm -rf dist/ build/ src/*.egg-info
python -m build
```

The resulting distribution archives will be placed in `dist/`:
- `dist/embeddeddb-0.1.0-py3-none-any.whl`
- `dist/embeddeddb-0.1.0.tar.gz`
