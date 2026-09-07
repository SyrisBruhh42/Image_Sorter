"""Bounded background image decoding and preload queue.

Implements SHARED DECODER CONTRACT v1 with priority scheduling, request identity,
cancellation, bounded backlogs, and nonblocking request_stop.
"""

from __future__ import annotations

import heapq
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any

from PyQt6.QtCore import QObject, QThread, pyqtSignal
from PyQt6.QtGui import QImage

from .image_decoding import decode_image
from .logger import logger


@dataclass(order=True)
class DecodeRequest:
    """Data class representing a queued decoding request.

    Ordered primarily by priority (ascending: 0 is highest priority) and
    secondarily by sequence number (FIFO for identical priority).
    """

    priority: int
    seq_num: int
    request_id: str = field(compare=False)
    generation: int = field(compare=False)
    filepath: str = field(compare=False)
    target_size: tuple[int, int] | None = field(compare=False, default=None)
    cancelled: bool = field(compare=False, default=False)


class ImageLoader(QThread):
    """Background QThread for asynchronous image preloading and decoding.

    Supports SHARED DECODER CONTRACT v1: priority-based request queueing,
    request identity tracking, nonblocking shutdown, auto-transform decoding,
    and bounded memory/queue accounting.
    """

    # SHARED DECODER CONTRACT v1 signal emitting result dictionary:
    # {"request_id": str, "generation": int, "filepath": str, "image": QImage | None, "error": str | None}
    image_ready = pyqtSignal(dict)

    # Maximum allowed pending preload requests in queue
    MAX_QUEUE_CAPACITY: int = 50

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._queue: list[DecodeRequest] = []
        self._requests_by_id: dict[str, DecodeRequest] = {}
        self._active_requests: dict[str, DecodeRequest] = {}
        self._running: bool = True
        self._current_generation: int = 0
        self._seq_counter: int = 0
        self._cond: threading.Condition = threading.Condition()

    def add_task(
        self,
        filepath: str,
        *,
        request_id: str | None = None,
        generation: int = 0,
        target_size: tuple[int, int] | None = None,
        priority: int = 0,
    ) -> str:
        """Adds an image decoding request to the preload queue.

        Args:
            filepath: Path to the image file.
            request_id: Optional explicit request identifier. Generated if None.
            generation: Folder/navigation generation integer identity.
            target_size: Optional (width, height) bounding box for downscaling previews.
            priority: Task priority. 0 indicates current foreground image; higher values
                      indicate lower priority preloads.

        Returns:
            String request_id for the submitted request.
        """
        if not filepath:
            return ""

        req_id: str = request_id if request_id else f"req_{uuid.uuid4().hex}"

        dropped: DecodeRequest | None = None
        with self._cond:
            if not self._running:
                return req_id

            # Update loader current generation if higher generation presented
            self._current_generation = max(self._current_generation, generation)

            # Coalesce duplicate pending work for identical filepath, target_size, generation
            for existing in self._queue:
                if (
                    not existing.cancelled
                    and existing.filepath == filepath
                    and existing.target_size == target_size
                    and existing.generation == generation
                ):
                    if priority < existing.priority:
                        existing.priority = priority
                        heapq.heapify(self._queue)
                    return existing.request_id

            self._seq_counter += 1
            req = DecodeRequest(
                priority=priority,
                seq_num=self._seq_counter,
                request_id=req_id,
                generation=generation,
                filepath=filepath,
                target_size=target_size,
            )

            heapq.heappush(self._queue, req)
            self._requests_by_id[req_id] = req

            # Keep the pending queue strictly bounded. Prefer discarding a low-priority
            # preload; if every request is foreground work, discard the oldest request
            # so rapid navigation cannot grow memory without limit.
            if len(self._queue) > self.MAX_QUEUE_CAPACITY:
                dropped = max(self._queue, key=lambda r: (r.priority, -r.seq_num))
                dropped.cancelled = True
                self._queue.remove(dropped)
                heapq.heapify(self._queue)
                self._requests_by_id.pop(dropped.request_id, None)

            self._cond.notify_all()

        if dropped is not None:
            self._emit_result(
                request_id=dropped.request_id,
                generation=dropped.generation,
                filepath=dropped.filepath,
                image=None,
                error="Request dropped because the decode queue is full",
            )
        return req_id

    def cancel_request(self, request_id: str) -> None:
        """Cancels a pending decode request by ID."""
        with self._cond:
            req = self._requests_by_id.get(request_id)
            if req:
                req.cancelled = True

    def clear_tasks(self, new_generation: int | None = None) -> None:
        """Flushes pending preload tasks.

        Args:
            new_generation: Optional new folder/navigation generation integer.
        """
        with self._cond:
            if new_generation is not None:
                self._current_generation = max(self._current_generation, new_generation)
            for req in self._queue:
                req.cancelled = True
            self._queue.clear()
            for request_id in list(self._requests_by_id):
                if request_id not in self._active_requests:
                    self._requests_by_id.pop(request_id, None)
            for req in self._active_requests.values():
                if new_generation is None or req.generation < self._current_generation:
                    req.cancelled = True
            self._cond.notify_all()

    def request_stop(self) -> None:
        """Nonblocking shutdown request for thread exit."""
        with self._cond:
            self._running = False
            for req in self._queue:
                req.cancelled = True
            for req in self._active_requests.values():
                req.cancelled = True
            self._queue.clear()
            for request_id in list(self._requests_by_id):
                if request_id not in self._active_requests:
                    self._requests_by_id.pop(request_id, None)
            self._cond.notify_all()

    def stop(self) -> None:
        """Legacy synchronous thread stop method."""
        self.request_stop()
        if not self.wait(5_000):
            logger.warning("Image decoder did not stop within five seconds")

    def run(self) -> None:
        """Main processing loop executing on the worker thread."""
        while True:
            with self._cond:
                while self._running and not self._queue:
                    self._cond.wait(timeout=0.05)

                if not self._running and not self._queue:
                    break

                if not self._queue:
                    continue

                req: DecodeRequest = heapq.heappop(self._queue)
                self._active_requests[req.request_id] = req

            # Check if request was cancelled or belongs to an obsolete generation
            if req.cancelled or req.generation < self._current_generation:
                self._emit_result(
                    request_id=req.request_id,
                    generation=req.generation,
                    filepath=req.filepath,
                    image=None,
                    error="Request cancelled",
                )
                self._finish_request(req.request_id)
                continue

            try:
                qimg, error = decode_image(req.filepath, target_size=req.target_size)

                # Re-check cancellation after potentially long decode
                if not self._running or req.cancelled or req.generation < self._current_generation:
                    self._emit_result(
                        request_id=req.request_id,
                        generation=req.generation,
                        filepath=req.filepath,
                        image=None,
                        error="Request cancelled during decode",
                    )
                    self._finish_request(req.request_id)
                    continue

                self._emit_result(
                    request_id=req.request_id,
                    generation=req.generation,
                    filepath=req.filepath,
                    image=qimg,
                    error=error,
                )
                self._finish_request(req.request_id)

            except Exception as exc:
                logger.error(
                    f"Error preloading image {req.filepath} (ID: {req.request_id}): {exc}",
                    exc_info=True,
                )
                self._emit_result(
                    request_id=req.request_id,
                    generation=req.generation,
                    filepath=req.filepath,
                    image=None,
                    error=str(exc),
                )
                self._finish_request(req.request_id)

    def _finish_request(self, request_id: str) -> None:
        """Remove terminal request bookkeeping without touching another request."""
        with self._cond:
            self._active_requests.pop(request_id, None)
            self._requests_by_id.pop(request_id, None)

    def _emit_result(
        self,
        request_id: str,
        generation: int,
        filepath: str,
        image: QImage | None,
        error: str | None,
    ) -> None:
        """Emit the sole public decoder result contract."""
        res: dict[str, Any] = {
            "request_id": request_id,
            "generation": generation,
            "filepath": filepath,
            "image": image,
            "error": error,
        }
        self.image_ready.emit(res)
