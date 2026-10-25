"""A lifetime OS lock enforces the single backend owner of a database."""

import os
from pathlib import Path


class ProcessLock:
    def __init__(self, database: Path):
        self.path = Path(str(database.resolve()) + ".lock")
        self.file = None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open("a+b")
        try:
            self.file.seek(0)
            if not self.file.read(1):
                self.file.write(b"0")
                self.file.flush()
            self.file.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            self.file = None
            raise RuntimeError("database already owned by another backend") from None

    def close(self):
        if self.file is not None:
            self.file.close()
            self.file = None
