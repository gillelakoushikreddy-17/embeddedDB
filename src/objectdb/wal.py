
import json
import os
from pathlib import Path


class WriteAheadLog:
    """A simple append-only Write-Ahead Log."""

    def __init__(self, file_path):
        self.file_path = Path(file_path)
        self.file = None
        self.next_lsn = 1



    def open(self):
        """Open the WAL, discard a truncated final record, and find the next LSN."""
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.file = open(self.file_path, "a+b")

        try:
            self.file.seek(0)
            last_lsn = 0

            while True:
                line_start = self.file.tell()
                line = self.file.readline()

                if not line:
                    break

                # An unterminated final line may be a partially written record.
                if not line.endswith(b"\n"):
                    self.file.seek(line_start)
                    self.file.truncate()
                    self.file.flush()
                    os.fsync(self.file.fileno())
                    break


                try:
                    record = json.loads(line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ValueError(
                        "WAL contains an invalid record"
                    ) from exc


                if not isinstance(record, dict):
                    raise ValueError("WAL contains an invalid record")

                if not {"lsn", "type", "data"}.issubset(record):
                    raise ValueError("WAL record is missing required fields")

                lsn = record["lsn"]

                if (
                        not isinstance(lsn, int)
                        or isinstance(lsn, bool)
                ):
                    raise ValueError("WAL record has an invalid LSN")

                if lsn <= last_lsn:
                    raise ValueError("WAL LSNs must be strictly increasing")

                record_type = record["type"]

                if not isinstance(record_type, str) or not record_type:
                    raise ValueError("WAL record has an invalid type")

                if not isinstance(record["data"], dict):
                    raise ValueError("WAL record data must be a dictionary")

                last_lsn = lsn

            self.next_lsn = last_lsn + 1
            self.file.seek(0, os.SEEK_END)

        except Exception:
            self.file.close()
            self.file = None
            raise



    def append(self, record_type, data):
        """Append a record and return its LSN."""
        if self.file is None:
            raise RuntimeError("WAL is not open")

        if not isinstance(record_type, str) or not record_type:
            raise ValueError("record_type must be a non-empty string")

        lsn = self.next_lsn

        record = {
            "lsn": lsn,
            "type": record_type,
            "data": data,
        }

        try:
            encoded = (
                json.dumps(record, separators=(",", ":")) + "\n"
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ValueError("WAL data must be JSON-serializable") from exc

        self.file.seek(0, os.SEEK_END)
        start_pos = self.file.tell()

        try:
            self.file.write(encoded)
            # Flush Python's buffer and request OS-level synchronization.
            self.file.flush()
            if hasattr(self.file, "fileno"):
                os.fsync(self.file.fileno())
            self.next_lsn += 1
            return lsn
        except Exception:
            try:
                self.file.seek(start_pos)
                self.file.truncate()
                self.file.flush()
            except Exception:
                pass
            raise

    def read_all(self):
        """Read all valid WAL records in LSN order."""
        if self.file is None:
            raise RuntimeError("WAL is not open")

        self.file.flush()
        self.file.seek(0)

        records = []

        while True:
            line = self.file.readline()

            if not line:
                break

            try:
                record = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("WAL contains an invalid record") from exc

            records.append(record)

        self.file.seek(0, os.SEEK_END)

        return records

    def close(self):
        """Close the WAL file."""
        if self.file is not None:
            self.file.close()
            self.file = None