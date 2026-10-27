"""
Persistent Tree Catalog for ObjectDB.

Manages the mapping of unique tree names to their corresponding B+ tree root page IDs.
The catalog is stored across one or more 4096-byte database pages linked via next_page_id.
All modifications to catalog pages use TransactionalPageWriter so that before-images are
recorded in the write-ahead log (WAL), ensuring full rollback and crash recovery support.
"""

from __future__ import annotations

import struct
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from objectdb.page_manager import PageManager

CATALOG_PAGE_TYPE = 0x03
PAGE_SIZE = 4096
MAX_TREE_NAME_LEN = 255

# Header format:
# page_type: uint8 (1 byte)
# flags: uint8 (1 byte)
# entry_count: uint16 (2 bytes)
# next_page_id: uint32 (4 bytes)
CATALOG_HEADER_FORMAT = "<BBHI"
CATALOG_HEADER_SIZE = struct.calcsize(CATALOG_HEADER_FORMAT)  # 8 bytes

# Entry fixed prefix format:
# name_len: uint16 (2 bytes)
# root_page_id: uint32 (4 bytes)
# flags: uint16 (2 bytes)
ENTRY_PREFIX_FORMAT = "<HIH"
ENTRY_PREFIX_SIZE = struct.calcsize(ENTRY_PREFIX_FORMAT)  # 8 bytes


class TreeAlreadyExistsError(ValueError):
    """Raised when attempting to create a tree with a name that already exists."""
    pass


class TreeNotFoundError(KeyError):
    """Raised when attempting to open a tree that does not exist."""
    pass


def validate_tree_name(name: str) -> None:
    """Validate that tree name meets format and length constraints."""
    if not isinstance(name, str) or isinstance(name, bool):
        raise TypeError("Tree name must be a string")
    if len(name) == 0:
        raise ValueError("Tree name cannot be empty")
    encoded = name.encode("utf-8")
    if len(encoded) > MAX_TREE_NAME_LEN:
        raise ValueError(
            f"Tree name exceeds maximum length of {MAX_TREE_NAME_LEN} bytes "
            f"({len(encoded)} bytes)"
        )


def encode_catalog_page(
    entries: list[tuple[str, int]],
    next_page_id: int = 0,
) -> bytes:
    """Serialize catalog entries and next_page_id into a 4096-byte database page."""
    if not isinstance(next_page_id, int) or isinstance(next_page_id, bool) or next_page_id < 0:
        raise ValueError("next_page_id must be a non-negative integer")

    header = struct.pack(
        CATALOG_HEADER_FORMAT,
        CATALOG_PAGE_TYPE,
        0,  # flags
        len(entries),
        next_page_id,
    )

    chunks = [header]
    total_size = CATALOG_HEADER_SIZE

    for name, root_page_id in entries:
        validate_tree_name(name)
        if not isinstance(root_page_id, int) or isinstance(root_page_id, bool) or root_page_id <= 0:
            raise ValueError(f"Root page ID must be a positive integer, got {root_page_id}")

        name_bytes = name.encode("utf-8")
        entry_prefix = struct.pack(
            ENTRY_PREFIX_FORMAT,
            len(name_bytes),
            root_page_id,
            0,  # flags
        )
        entry_data = entry_prefix + name_bytes
        total_size += len(entry_data)
        if total_size > PAGE_SIZE:
            raise ValueError(
                f"Catalog page capacity exceeded: {total_size} bytes > {PAGE_SIZE} bytes"
            )
        chunks.append(entry_data)

    payload = b"".join(chunks)
    return payload.ljust(PAGE_SIZE, b"\x00")


def decode_catalog_page(data: bytes) -> tuple[list[tuple[str, int]], int]:
    """
    Deserialize a 4096-byte database page into catalog entries and next_page_id.
    Returns (entries, next_page_id).
    """
    if len(data) != PAGE_SIZE:
        raise ValueError(f"Catalog page data must be exactly {PAGE_SIZE} bytes, got {len(data)}")

    page_type, flags, entry_count, next_page_id = struct.unpack_from(
        CATALOG_HEADER_FORMAT,
        data,
        0,
    )

    if page_type != CATALOG_PAGE_TYPE:
        raise ValueError(
            f"Invalid catalog page type: expected {CATALOG_PAGE_TYPE}, got {page_type}"
        )

    entries = []
    offset = CATALOG_HEADER_SIZE

    for _ in range(entry_count):
        if offset + ENTRY_PREFIX_SIZE > PAGE_SIZE:
            raise ValueError("Corrupted catalog page: truncated entry prefix")

        name_len, root_page_id, entry_flags = struct.unpack_from(
            ENTRY_PREFIX_FORMAT,
            data,
            offset,
        )
        offset += ENTRY_PREFIX_SIZE

        if offset + name_len > PAGE_SIZE:
            raise ValueError("Corrupted catalog page: truncated entry name")

        name_bytes = data[offset : offset + name_len]
        try:
            name = name_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("Corrupted catalog page: invalid UTF-8 tree name") from exc

        offset += name_len
        entries.append((name, root_page_id))

    return entries, next_page_id


class TreeCatalog:
    """Persistent catalog maintaining the list of trees and their root page IDs."""

    def __init__(self, page_manager: PageManager, catalog_page_id: int = 0):
        self.page_manager = page_manager
        self.catalog_page_id = catalog_page_id
        # Map: name -> {"root_page_id": int, "page_id": int, "entry_index": int}
        self._entries: dict[str, dict[str, int]] = {}
        # List of catalog page IDs in order of chain
        self._page_chain: list[int] = []
        # Map: page_id -> list of (name, root_page_id)
        self._page_entries: dict[int, list[tuple[str, int]]] = {}
        # Map: page_id -> next_page_id (for O(1) page linkage lookup)
        self._page_next: dict[int, int] = {}
        self._is_loaded = False

    def load(self) -> None:
        """Load all catalog pages from disk into memory."""
        self._entries.clear()
        self._page_chain.clear()
        self._page_entries.clear()
        self._page_next.clear()

        if self.catalog_page_id == 0:
            self._is_loaded = True
            return

        curr_page_id = self.catalog_page_id
        visited: set[int] = set()

        while curr_page_id != 0:
            if curr_page_id in visited:
                raise ValueError(f"Cycle detected in catalog page chain at page {curr_page_id}")
            visited.add(curr_page_id)

            data = self.page_manager.read_page(curr_page_id)
            entries, next_page_id = decode_catalog_page(data)

            self._page_chain.append(curr_page_id)
            self._page_entries[curr_page_id] = list(entries)
            self._page_next[curr_page_id] = next_page_id

            for idx, (name, root_page_id) in enumerate(entries):
                if name in self._entries:
                    raise ValueError(f"Corrupted catalog: duplicate tree name '{name}' found")
                self._entries[name] = {
                    "root_page_id": root_page_id,
                    "page_id": curr_page_id,
                    "entry_index": idx,
                }

            curr_page_id = next_page_id

        self._is_loaded = True

    def get_root(self, name: str) -> int:
        """Get the root page ID for a named tree."""
        validate_tree_name(name)
        if name not in self._entries:
            raise TreeNotFoundError(f"Tree '{name}' does not exist")
        return self._entries[name]["root_page_id"]

    def has_tree(self, name: str) -> bool:
        """Check if a tree exists in the catalog."""
        return name in self._entries

    def list_trees(self) -> list[str]:
        """Return a sorted list of all tree names."""
        return sorted(self._entries.keys())

    def get_catalog_pages(self) -> list[int]:
        """Return the list of all page IDs belonging to the catalog chain."""
        return list(self._page_chain)

    def _entry_bytes_size(self, name: str) -> int:
        return ENTRY_PREFIX_SIZE + len(name.encode("utf-8"))

    def create_tree(
        self,
        name: str,
        root_page_id: int,
        writer,
        tx_id: int | None = None,
    ) -> None:
        """
        Register a new tree in the persistent catalog.
        Allocates the first catalog page or an additional catalog page if needed.
        """
        validate_tree_name(name)
        if self.has_tree(name):
            raise TreeAlreadyExistsError(f"Tree '{name}' already exists")

        new_entry_size = self._entry_bytes_size(name)

        # Case 1: First catalog page has not been allocated yet
        if self.catalog_page_id == 0:
            if tx_id is not None:
                cat_page_id = writer.allocate_page(tx_id)
            else:
                cat_page_id = self.page_manager.allocate_page()

            page_data = encode_catalog_page([(name, root_page_id)], next_page_id=0)

            if tx_id is not None:
                writer.write_page(tx_id, cat_page_id, page_data)
                writer.update_catalog_page_id(tx_id, cat_page_id)
            else:
                self.page_manager.write_page(cat_page_id, page_data)
                self.page_manager.update_catalog_page_id(cat_page_id)

            self.catalog_page_id = cat_page_id
            self._page_chain = [cat_page_id]
            self._page_entries[cat_page_id] = [(name, root_page_id)]
            self._page_next[cat_page_id] = 0
            self._entries[name] = {
                "root_page_id": root_page_id,
                "page_id": cat_page_id,
                "entry_index": 0,
            }
            return

        # Case 2: Catalog already exists; check if the last page has capacity
        last_page_id = self._page_chain[-1]
        current_entries = list(self._page_entries[last_page_id])

        current_size = CATALOG_HEADER_SIZE + sum(
            self._entry_bytes_size(n) for n, _ in current_entries
        )

        if current_size + new_entry_size <= PAGE_SIZE:
            # Fits in current last page
            current_entries.append((name, root_page_id))
            page_data = encode_catalog_page(current_entries, next_page_id=0)

            if tx_id is not None:
                writer.write_page(tx_id, last_page_id, page_data)
            else:
                self.page_manager.write_page(last_page_id, page_data)

            self._page_entries[last_page_id] = current_entries
            self._page_next[last_page_id] = 0
            self._entries[name] = {
                "root_page_id": root_page_id,
                "page_id": last_page_id,
                "entry_index": len(current_entries) - 1,
            }
        else:
            # Need a new catalog page in the chain
            if tx_id is not None:
                new_page_id = writer.allocate_page(tx_id)
            else:
                new_page_id = self.page_manager.allocate_page()

            # 1. Write the new page
            new_page_data = encode_catalog_page([(name, root_page_id)], next_page_id=0)
            if tx_id is not None:
                writer.write_page(tx_id, new_page_id, new_page_data)
            else:
                self.page_manager.write_page(new_page_id, new_page_data)

            # 2. Link previous last page to new page
            linked_page_data = encode_catalog_page(current_entries, next_page_id=new_page_id)
            if tx_id is not None:
                writer.write_page(tx_id, last_page_id, linked_page_data)
            else:
                self.page_manager.write_page(last_page_id, linked_page_data)

            self._page_chain.append(new_page_id)
            self._page_entries[new_page_id] = [(name, root_page_id)]
            self._page_next[last_page_id] = new_page_id
            self._page_next[new_page_id] = 0
            self._entries[name] = {
                "root_page_id": root_page_id,
                "page_id": new_page_id,
                "entry_index": 0,
            }

    def update_root(
        self,
        name: str,
        new_root_page_id: int,
        writer,
        tx_id: int | None = None,
    ) -> None:
        """Update the root page ID for an existing tree."""
        if not self.has_tree(name):
            raise TreeNotFoundError(f"Tree '{name}' does not exist")
        if not isinstance(new_root_page_id, int) or isinstance(new_root_page_id, bool) or new_root_page_id <= 0:
            raise ValueError(f"Root page ID must be a positive integer, got {new_root_page_id}")

        info = self._entries[name]
        page_id = info["page_id"]
        entry_idx = info["entry_index"]

        entries = list(self._page_entries[page_id])
        entries[entry_idx] = (name, new_root_page_id)

        next_page_id = self._page_next.get(page_id, 0)
        page_data = encode_catalog_page(entries, next_page_id=next_page_id)

        if tx_id is not None:
            writer.write_page(tx_id, page_id, page_data)
        else:
            self.page_manager.write_page(page_id, page_data)

        self._page_entries[page_id] = entries
        self._entries[name]["root_page_id"] = new_root_page_id

    def drop_tree(
        self,
        name: str,
        writer,
        tx_id: int | None = None,
    ) -> bool:
        """Remove a tree from the catalog."""
        if not self.has_tree(name):
            return False

        info = self._entries[name]
        page_id = info["page_id"]

        entries = [(n, r) for n, r in self._page_entries[page_id] if n != name]

        next_page_id = self._page_next.get(page_id, 0)
        page_data = encode_catalog_page(entries, next_page_id=next_page_id)

        if tx_id is not None:
            writer.write_page(tx_id, page_id, page_data)
        else:
            self.page_manager.write_page(page_id, page_data)

        self._page_entries[page_id] = entries
        del self._entries[name]

        # Re-index entry indices for that page
        for idx, (n, r) in enumerate(entries):
            self._entries[n]["entry_index"] = idx

        return True
