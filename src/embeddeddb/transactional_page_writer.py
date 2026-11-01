
import base64

from embeddeddb.page_manager import PAGE_SIZE


class TransactionalPageWriter:
    """Write database pages after recording their before-images in WAL."""

    def __init__(self, page_manager, wal):
        self.page_manager = page_manager
        self.wal = wal

    def write_page(self, transaction_id, page_id, new_data):
        """Log the original page before writing its replacement."""

        if (
            not isinstance(transaction_id, int)
            or isinstance(transaction_id, bool)
            or transaction_id <= 0
        ):
            raise ValueError("Transaction ID must be a positive integer")

        if (
            not isinstance(page_id, int)
            or isinstance(page_id, bool)
            or page_id <= 0
        ):
            raise ValueError("Page ID must be a positive integer")

        if not isinstance(new_data, bytes) or len(new_data) != PAGE_SIZE:
            raise ValueError("Page data must be exactly 4096 bytes")

        # Read the original page before changing it.
        original_data = self.page_manager.read_page(page_id)

        # Base64 safely represents binary data inside a JSON WAL record.
        encoded_original = base64.b64encode(original_data).decode("ascii")

        # The WAL append must succeed before the page is overwritten.
        self.wal.append(
            "PAGE_BEFORE_IMAGE",
            {
                "transaction_id": transaction_id,
                "page_id": page_id,
                "original_data": encoded_original,
            },
        )

        # Only now modify the database page.
        self.page_manager.write_page(page_id, new_data)

    def allocate_page(self, transaction_id):
        """Allocate a page and record its allocation in the WAL."""
        if (
                not isinstance(transaction_id, int)
                or isinstance(transaction_id, bool)
                or transaction_id <= 0
        ):
            raise ValueError("Transaction ID must be a positive integer")

        # Determine which page will be allocated.
        self.page_manager.file.seek(0, 2)
        file_size = self.page_manager.file.tell()

        from embeddeddb.page_manager import PAGE_SIZE

        if file_size % PAGE_SIZE != 0:
            raise ValueError("Database file contains an incomplete page")

        page_id = file_size // PAGE_SIZE

        # Log the intended allocation before modifying the database file.
        self.wal.append(
            "PAGE_ALLOCATED",
            {
                "transaction_id": transaction_id,
                "page_id": page_id,
            },
        )

        # Now allocate the page.
        actual_page_id = self.page_manager.allocate_page()

        if actual_page_id != page_id:
            raise RuntimeError("Allocated page ID does not match WAL record")

        return actual_page_id




    def restore_page(self, page_id, encoded_original):
        """Restore a page from a Base64-encoded before-image."""

        if (
            not isinstance(page_id, int)
            or isinstance(page_id, bool)
            or page_id <= 0
        ):
            raise ValueError("Page ID must be a positive integer")

        try:
            original_data = base64.b64decode(
                encoded_original,
                validate=True,
            )
        except (ValueError, TypeError) as exc:
            raise ValueError("Invalid Base64 before-image") from exc

        if len(original_data) != PAGE_SIZE:
            raise ValueError("Before-image must decode to exactly 4096 bytes")

        self.page_manager.write_page(page_id, original_data)

    def update_root_page_id(self, transaction_id, new_root_page_id):
        """Log the current root page ID before updating it in the header."""
        if (
            not isinstance(transaction_id, int)
            or isinstance(transaction_id, bool)
            or transaction_id <= 0
        ):
            raise ValueError("Transaction ID must be a positive integer")

        if (
            not isinstance(new_root_page_id, int)
            or isinstance(new_root_page_id, bool)
            or new_root_page_id < 0
        ):
            raise ValueError("Root page ID must be a non-negative integer")

        from embeddeddb.header import read_header
        page_zero = self.page_manager.read_page(0)
        header = read_header(page_zero)
        original_root_page_id = header["root_page_id"]

        self.wal.append(
            "ROOT_PAGE_BEFORE_IMAGE",
            {
                "transaction_id": transaction_id,
                "original_root_page_id": original_root_page_id,
                "new_root_page_id": new_root_page_id,
            },
        )

        self.page_manager.update_root_page_id(new_root_page_id)

    def restore_root_page_id(self, original_root_page_id):
        """Restore the root page ID from a recorded before-image."""
        if (
            not isinstance(original_root_page_id, int)
            or isinstance(original_root_page_id, bool)
            or original_root_page_id < 0
        ):
            raise ValueError("Root page ID must be a non-negative integer")

        self.page_manager.update_root_page_id(original_root_page_id)

    def update_catalog_page_id(self, transaction_id, new_catalog_page_id):
        """Log the current catalog page ID before updating it in the header."""
        if (
            not isinstance(transaction_id, int)
            or isinstance(transaction_id, bool)
            or transaction_id <= 0
        ):
            raise ValueError("Transaction ID must be a positive integer")

        if (
            not isinstance(new_catalog_page_id, int)
            or isinstance(new_catalog_page_id, bool)
            or new_catalog_page_id < 0
        ):
            raise ValueError("Catalog page ID must be a non-negative integer")

        from embeddeddb.header import read_header
        page_zero = self.page_manager.read_page(0)
        header = read_header(page_zero)
        original_catalog_page_id = header.get("catalog_page_id", 0)

        self.wal.append(
            "CATALOG_PAGE_BEFORE_IMAGE",
            {
                "transaction_id": transaction_id,
                "original_catalog_page_id": original_catalog_page_id,
                "new_catalog_page_id": new_catalog_page_id,
            },
        )

        self.page_manager.update_catalog_page_id(new_catalog_page_id)

    def restore_catalog_page_id(self, original_catalog_page_id):
        """Restore the catalog page ID from a recorded before-image."""
        if (
            not isinstance(original_catalog_page_id, int)
            or isinstance(original_catalog_page_id, bool)
            or original_catalog_page_id < 0
        ):
            raise ValueError("Catalog page ID must be a non-negative integer")

        self.page_manager.update_catalog_page_id(original_catalog_page_id)

    def sync(self):
        """Make database page changes durable."""
        self.page_manager.sync()

    def reclaim_allocated_pages(self, page_ids):
        """Reclaim uncommitted allocated pages from the underlying page manager."""
        if hasattr(self.page_manager, "reclaim_allocated_pages"):
            self.page_manager.reclaim_allocated_pages(page_ids)


