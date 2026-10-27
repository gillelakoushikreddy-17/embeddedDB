
import base64
import binascii

from objectdb.page_manager import PAGE_SIZE


class RecoveryManager:
    """Analyze WAL records and recover incomplete transactions."""

    VALID_RECORD_TYPES = {
        "BEGIN",
        "COMMIT",
        "ROLLBACK",
        "PAGE_BEFORE_IMAGE",
        "PAGE_ALLOCATED",
        "ROOT_PAGE_BEFORE_IMAGE",
        "CATALOG_PAGE_BEFORE_IMAGE",
    }

    def __init__(self, wal):
        self.wal = wal

    def analyze(self):
        """Identify committed, rolled-back, and incomplete transactions."""
        records = self.wal.read_all()
        transactions = {}

        for record in records:
            record_type = record.get("type")
            data = record.get("data", {})

            if not isinstance(data, dict):
                raise ValueError(
                    "WAL record data must be a dictionary"
                )

            if record_type not in self.VALID_RECORD_TYPES:
                raise ValueError(
                    f"Unknown WAL record type: {record_type}"
                )

            transaction_id = data.get("transaction_id")

            if (
                not isinstance(transaction_id, int)
                or isinstance(transaction_id, bool)
                or transaction_id <= 0
            ):
                raise ValueError(
                    "WAL record contains an invalid transaction ID"
                )

            if transaction_id not in transactions:
                transactions[transaction_id] = {
                    "begun": False,
                    "committed": False,
                    "rolled_back": False,
                    "before_images": [],
                    "allocated_pages": [],
                    "root_before_images": [],
                    "catalog_before_images": [],
                }

            transaction = transactions[transaction_id]

            if record_type == "BEGIN":
                if (
                    transaction["committed"]
                    or transaction["rolled_back"]
                ):
                    raise ValueError(
                        "Transaction ID reused after completion"
                    )

                if transaction["begun"]:
                    raise ValueError("Duplicate BEGIN")

                transaction["begun"] = True

            elif record_type == "COMMIT":
                if not transaction["begun"]:
                    raise ValueError("COMMIT without BEGIN")

                if transaction["rolled_back"]:
                    raise ValueError("COMMIT after ROLLBACK")

                if transaction["committed"]:
                    raise ValueError("Duplicate COMMIT")

                transaction["committed"] = True

            elif record_type == "ROLLBACK":
                if not transaction["begun"]:
                    raise ValueError("ROLLBACK without BEGIN")

                if transaction["committed"]:
                    raise ValueError("ROLLBACK after COMMIT")

                if transaction["rolled_back"]:
                    raise ValueError("Duplicate ROLLBACK")

                transaction["rolled_back"] = True

            elif record_type == "PAGE_BEFORE_IMAGE":
                if not transaction["begun"]:
                    raise ValueError(
                        "PAGE_BEFORE_IMAGE without BEGIN"
                    )

                if transaction["committed"]:
                    raise ValueError(
                        "PAGE_BEFORE_IMAGE after COMMIT"
                    )

                if transaction["rolled_back"]:
                    raise ValueError(
                        "PAGE_BEFORE_IMAGE after ROLLBACK"
                    )

                page_id = data.get("page_id")

                if (
                    not isinstance(page_id, int)
                    or isinstance(page_id, bool)
                    or page_id <= 0
                ):
                    raise ValueError(
                        "Invalid page ID in before-image"
                    )

                original_data = data.get("original_data")

                if not isinstance(original_data, str):
                    raise ValueError(
                        "Before-image data must be a Base64 string"
                    )

                try:
                    decoded_data = base64.b64decode(
                        original_data,
                        validate=True,
                    )
                except (binascii.Error, ValueError) as exc:
                    raise ValueError(
                        "Invalid Base64 before-image"
                    ) from exc

                if len(decoded_data) != PAGE_SIZE:
                    raise ValueError(
                        "Before-image must decode to exactly "
                        f"{PAGE_SIZE} bytes"
                    )

                transaction["before_images"].append(record)

            elif record_type == "PAGE_ALLOCATED":
                if not transaction["begun"]:
                    raise ValueError(
                        "PAGE_ALLOCATED without BEGIN"
                    )

                if transaction["committed"]:
                    raise ValueError(
                        "PAGE_ALLOCATED after COMMIT"
                    )

                if transaction["rolled_back"]:
                    raise ValueError(
                        "PAGE_ALLOCATED after ROLLBACK"
                    )

                page_id = data.get("page_id")

                if (
                    not isinstance(page_id, int)
                    or isinstance(page_id, bool)
                    or page_id <= 0
                ):
                    raise ValueError(
                        "Invalid allocated page ID"
                    )

                if page_id in transaction["allocated_pages"]:
                    raise ValueError(
                        "Duplicate PAGE_ALLOCATED record"
                    )

                transaction["allocated_pages"].append(page_id)

            elif record_type == "ROOT_PAGE_BEFORE_IMAGE":
                if not transaction["begun"]:
                    raise ValueError(
                        "ROOT_PAGE_BEFORE_IMAGE without BEGIN"
                    )

                if transaction["committed"]:
                    raise ValueError(
                        "ROOT_PAGE_BEFORE_IMAGE after COMMIT"
                    )

                if transaction["rolled_back"]:
                    raise ValueError(
                        "ROOT_PAGE_BEFORE_IMAGE after ROLLBACK"
                    )

                orig_root = data.get("original_root_page_id")
                if (
                    not isinstance(orig_root, int)
                    or isinstance(orig_root, bool)
                    or orig_root < 0
                ):
                    raise ValueError(
                        "Invalid original root page ID in before-image"
                    )

                new_root = data.get("new_root_page_id")
                if (
                    not isinstance(new_root, int)
                    or isinstance(new_root, bool)
                    or new_root < 0
                ):
                    raise ValueError(
                        "Invalid new root page ID in before-image"
                    )

                transaction["root_before_images"].append(data)

            elif record_type == "CATALOG_PAGE_BEFORE_IMAGE":
                if not transaction["begun"]:
                    raise ValueError(
                        "CATALOG_PAGE_BEFORE_IMAGE without BEGIN"
                    )

                if transaction["committed"]:
                    raise ValueError(
                        "CATALOG_PAGE_BEFORE_IMAGE after COMMIT"
                    )

                if transaction["rolled_back"]:
                    raise ValueError(
                        "CATALOG_PAGE_BEFORE_IMAGE after ROLLBACK"
                    )

                orig_cat = data.get("original_catalog_page_id")
                if (
                    not isinstance(orig_cat, int)
                    or isinstance(orig_cat, bool)
                    or orig_cat < 0
                ):
                    raise ValueError(
                        "Invalid original catalog page ID in before-image"
                    )

                new_cat = data.get("new_catalog_page_id")
                if (
                    not isinstance(new_cat, int)
                    or isinstance(new_cat, bool)
                    or new_cat < 0
                ):
                    raise ValueError(
                        "Invalid new catalog page ID in before-image"
                    )

                transaction["catalog_before_images"].append(data)

        committed = []
        rolled_back = []
        incomplete = []

        for transaction_id, transaction in transactions.items():
            if transaction["committed"]:
                committed.append(transaction_id)
            elif transaction["rolled_back"]:
                rolled_back.append(transaction_id)
            elif transaction["begun"]:
                incomplete.append(transaction_id)

        return {
            "transactions": transactions,
            "committed": committed,
            "rolled_back": rolled_back,
            "incomplete": incomplete,
        }

    def recover(self, page_writer):
        """
        Restore before-images for incomplete transactions.

        Allocated pages are reported for future reclamation handling,
        but this method does not physically remove them.
        """
        analysis = self.analyze()
        incomplete_transactions = set(analysis["incomplete"])
        records = self.wal.read_all()

        before_images = []
        allocated_pages = []
        root_before_images = []
        catalog_before_images = []

        for record in records:
            record_type = record.get("type")
            data = record.get("data", {})
            transaction_id = data.get("transaction_id")

            if transaction_id not in incomplete_transactions:
                continue

            if record_type == "PAGE_BEFORE_IMAGE":
                before_images.append(data)

            elif record_type == "PAGE_ALLOCATED":
                allocated_pages.append(data)

            elif record_type == "ROOT_PAGE_BEFORE_IMAGE":
                root_before_images.append(data)

            elif record_type == "CATALOG_PAGE_BEFORE_IMAGE":
                catalog_before_images.append(data)

        incomplete_allocated_ids = {data["page_id"] for data in allocated_pages}

        # Restore modified pages and root page ID in reverse WAL order.
        # Skip before-images for pages newly allocated in incomplete transactions,
        # as those uncommitted pages will be physically reclaimed.
        for record in reversed(records):
            record_type = record.get("type")
            data = record.get("data", {})
            transaction_id = data.get("transaction_id")

            if transaction_id not in incomplete_transactions:
                continue

            if record_type == "PAGE_BEFORE_IMAGE":
                page_id = data["page_id"]
                if page_id in incomplete_allocated_ids:
                    continue
                page_writer.restore_page(
                    page_id,
                    data["original_data"],
                )
            elif record_type == "ROOT_PAGE_BEFORE_IMAGE":
                if hasattr(page_writer, "restore_root_page_id"):
                    page_writer.restore_root_page_id(
                        data["original_root_page_id"],
                    )
            elif record_type == "CATALOG_PAGE_BEFORE_IMAGE":
                if hasattr(page_writer, "restore_catalog_page_id"):
                    page_writer.restore_catalog_page_id(
                        data["original_catalog_page_id"],
                    )

        # Physically reclaim uncommitted allocated pages from the database file
        if allocated_pages and hasattr(page_writer, "reclaim_allocated_pages"):
            page_writer.reclaim_allocated_pages(
                [data["page_id"] for data in allocated_pages]
            )

        if before_images or root_before_images or catalog_before_images or allocated_pages:
            page_writer.sync()

        return {
            "recovered_transactions": sorted(
                incomplete_transactions
            ),
            "restored_pages": len(before_images),
            "allocated_pages": [
                {
                    "transaction_id": data["transaction_id"],
                    "page_id": data["page_id"],
                }
                for data in allocated_pages
            ],
        }
