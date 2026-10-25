
from enum import Enum, auto


class TransactionState(Enum):
    ACTIVE = auto()
    COMMITTED = auto()
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

    def commit(self):
        """Commit the transaction."""
        self._ensure_active()

        # Make database page changes durable first.
        self.page_writer.sync()

        # Record the commit only after syncing the database.
        self.wal.append(
            "COMMIT",
            {"transaction_id": self.transaction_id},
        )

        self.state = TransactionState.COMMITTED

    def rollback(self):
        """Restore all modified pages in reverse log order."""
        self._ensure_active()

        records = self.wal.read_all()

        before_images = [
            record["data"]
            for record in records
            if record.get("type") == "PAGE_BEFORE_IMAGE"
            and record.get("data", {}).get("transaction_id")
            == self.transaction_id
        ]

        # Restore the most recent before-image first.
        for record_data in reversed(before_images):
            self.page_writer.restore_page(
                record_data["page_id"],
                record_data["original_data"],
            )
        # Ensure restored pages are durable before recording rollback.
        self.page_writer.sync()

        self.wal.append(
            "ROLLBACK",
            {"transaction_id": self.transaction_id},
        )

        self.state = TransactionState.ABORTED
