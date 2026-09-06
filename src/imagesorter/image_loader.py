"""Background thread for preloading images to achieve zero-latency navigation.

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

    # Legacy signal emitted when an image is decoded successfully
    image_loaded = pyqtSignal(str, QImage)

    # SHARED DECODER CONTRACT v1 signal emitting result dictionary:
    # {"request_id": str, "generation": int, "filepath": str, "image": QImage | None, "error": str | None}
    image_ready = pyqtSignal(dict)

    # Maximum allowed pending preload requests in queue
    MAX_QUEUE_CAPACITY: int = 50

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._queue: list[DecodeRequest] = []
        self._requests_by_id: dict[str, DecodeRequest] = {}
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

            # Enforce bounded backlog: drop lowest-priority (largest priority number)
            # pending preloads if queue exceeds capacity
            if len(self._queue) > self.MAX_QUEUE_CAPACITY:
                candidates = [
                    r for r in self._queue if r.priority > 0 and not r.cancelled
                ]
                if candidates:
                    worst_req = max(candidates, key=lambda r: (r.priority, r.seq_num))
                    worst_req.cancelled = True
                    self._queue = [r for r in self._queue if not r.cancelled]
                    heapq.heapify(self._queue)
                    self._requests_by_id.pop(worst_req.request_id, None)

            self._cond.notify_all()
            return req_id

    def cancel_request(self, request_id: str) -> None:
        """Cancels a pending decode request by ID."""
        with self._cond:
            req = self._requests_by_id.pop(request_id, None)
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
            self._requests_by_id.clear()
            self._cond.notify_all()

    def request_stop(self) -> None:
        """Nonblocking shutdown request for thread exit."""
        with self._cond:
            self._running = False
            for req in self._queue:
                req.cancelled = True
            self._queue.clear()
            self._requests_by_id.clear()
            self._cond.notify_all()

    def stop(self) -> None:
        """Legacy synchronous thread stop method."""
        self.request_stop()
        self.wait()

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
                self._requests_by_id.pop(req.request_id, None)

            # Check if request was cancelled or belongs to an obsolete generation
            if req.cancelled or req.generation < self._current_generation:
                self._emit_result(
                    request_id=req.request_id,
                    generation=req.generation,
                    filepath=req.filepath,
                    image=None,
                    error="Request cancelled",
                )
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
                    continue

                self._emit_result(
                    request_id=req.request_id,
                    generation=req.generation,
                    filepath=req.filepath,
                    image=qimg,
                    error=error,
                )

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

    def _emit_result(
        self,
        request_id: str,
        generation: int,
        filepath: str,
        image: QImage | None,
        error: str | None,
    ) -> None:
        """Helper to emit image_ready dict and legacy image_loaded signal."""
        res: dict[str, Any] = {
            "request_id": request_id,
            "generation": generation,
            "filepath": filepath,
            "image": image,
            "error": error,
        }
        self.image_ready.emit(res)
        if image is not None:
            self.image_loaded.emit(filepath, image)
