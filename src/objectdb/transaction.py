
from enum import Enum, auto


class TransactionState(Enum):
    ACTIVE = auto()
    COMMITTING = auto()
    COMMITTED = auto()
    FAILED = auto()
    ABORTED = auto()


class Transaction:
    """Manage a transaction and roll back its page changes."""

    def __init__(self, transaction_id, wal, page_writer):
        if (
            not isinstance(transaction_id, int)
            or isinstance(transaction_id, bool)
            or transaction_id <= 0
        ):
            raise ValueError("Transaction ID must be a positive integer")

        self.transaction_id = transaction_id
        self.wal = wal
        self.page_writer = page_writer
        self.state = TransactionState.ACTIVE

        self.wal.append(
            "BEGIN",
            {"transaction_id": self.transaction_id},
        )

    def _ensure_active(self):
        if self.state != TransactionState.ACTIVE:
            raise RuntimeError(
                f"Transaction is already {self.state.name.lower()}"
            )

    def write_page(self, page_id, new_data):
        """Write a page through the transactional page writer."""
        self._ensure_active()

        self.page_writer.write_page(
            self.transaction_id,
            page_id,
            new_data,
        )

    def allocate_page(self):
        """Allocate a page through the transactional page writer."""
        self._ensure_active()

        return self.page_writer.allocate_page(self.transaction_id)

    def update_root_page_id(self, root_page_id):
        """Update the root page ID through the transactional page writer."""
        self._ensure_active()

        self.page_writer.update_root_page_id(
            self.transaction_id,
            root_page_id,
        )

    def read_page(self, page_id):
        """Read a page through the underlying page manager."""
        return self.page_writer.page_manager.read_page(page_id)

    def commit(self):
        """Commit the transaction."""
        self._ensure_active()
        self.state = TransactionState.COMMITTING

        try:
            # Make database page changes durable first.
            self.page_writer.sync()

            # Record the commit only after syncing the database.
            self.wal.append(
                "COMMIT",
                {"transaction_id": self.transaction_id},
            )
            self.state = TransactionState.COMMITTED
        except Exception:
            self.state = TransactionState.FAILED
            raise

    def rollback(self):
        """Restore all modified pages and root page ID in reverse log order."""
        if self.state in (TransactionState.COMMITTING, TransactionState.FAILED):
            raise RuntimeError(
                f"Cannot roll back transaction in {self.state.name} state; outcome may be uncertain"
            )
        self._ensure_active()

        try:
            records = self.wal.read_all()

            tx_records = [
                record
                for record in records
                if record.get("data", {}).get("transaction_id")
                == self.transaction_id
            ]

            # Restore the most recent before-images first.
            for record in reversed(tx_records):
                record_type = record.get("type")
                record_data = record.get("data", {})

                if record_type == "PAGE_BEFORE_IMAGE":
                    self.page_writer.restore_page(
                        record_data["page_id"],
                        record_data["original_data"],
                    )
                elif record_type == "ROOT_PAGE_BEFORE_IMAGE":
                    if hasattr(self.page_writer, "restore_root_page_id"):
                        self.page_writer.restore_root_page_id(
                            record_data["original_root_page_id"],
                        )

            # Ensure restored pages are durable before recording rollback.
            self.page_writer.sync()

            self.wal.append(
                "ROLLBACK",
                {"transaction_id": self.transaction_id},
            )

            self.state = TransactionState.ABORTED
        except Exception:
            self.state = TransactionState.FAILED
            raise


class TransactionPageAdapter:
    """
    Adapts a Transaction to the page manager interface required by PersistentBTree.
    Routes page reads, page writes, page allocations, and root updates through the transaction.
    """

    def __init__(self, transaction, page_manager, tree=None):
        self.transaction = transaction
        self.page_manager = page_manager
        self.tree = tree

    def read_page(self, page_id):
        return self.page_manager.read_page(page_id)

    def write_page(self, page_id, data):
        self.transaction.write_page(page_id, data)

    def allocate_page(self):
        return self.transaction.allocate_page()

    def update_root_page_id(self, root_page_id):
        self.transaction.update_root_page_id(root_page_id)
        if self.tree is not None:
            self.tree.root_page_id = root_page_id

    @property
    def file(self):
        return self.page_manager.file
