# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A pipe is owned by its reader or caller cleanup, even during interrupted startup."""

import threading
from collections.abc import Callable
from contextlib import suppress
from typing import IO, Protocol


class PipeReader:
    def __init__(self, pipe: IO[bytes], consume: Callable[[bytes], None], chunk_size: int):
        self.pipe, self.consume, self.chunk_size = pipe, consume, chunk_size
        self._lock = threading.Lock()
        self._claimed = self._cancelled = False

    def pump(self) -> None:
        with self._lock:
            if self._cancelled:
                return
            self._claimed = True
        try:
            while chunk := self.pipe.read1(self.chunk_size):  # type: ignore[attr-defined]
                self.consume(chunk)
        except (OSError, ValueError):
            pass
        finally:
            with suppress(OSError):
                self.pipe.close()

    def close_unclaimed(self) -> None:
        with self._lock:
            if not self._claimed:
                self._cancelled = True
                with suppress(OSError):
                    self.pipe.close()


class PipeReaderOwner(Protocol):
    _dokimi_pipe_readers: dict[IO[bytes], PipeReader]
