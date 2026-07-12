from __future__ import annotations

import hashlib
import os
from collections import OrderedDict
from threading import Lock
from typing import Optional


MAX_SOURCE_IMAGES = max(1, int(os.environ.get("EASYCAD_SOURCE_IMAGE_CACHE_ITEMS", "32")))
MAX_SOURCE_IMAGE_BYTES = max(1, int(os.environ.get("EASYCAD_SOURCE_IMAGE_CACHE_BYTES", str(64 * 1024 * 1024))))

_images: OrderedDict[str, bytes] = OrderedDict()
_total_bytes = 0
_lock = Lock()


def store_source_image(data: bytes) -> tuple[str, str]:
    digest = hashlib.sha256(data).hexdigest()
    reference = f"memory://sha256/{digest}"
    global _total_bytes

    with _lock:
        previous = _images.pop(reference, None)
        if previous is not None:
            _total_bytes -= len(previous)
        _images[reference] = data
        _total_bytes += len(data)
        while len(_images) > MAX_SOURCE_IMAGES or _total_bytes > MAX_SOURCE_IMAGE_BYTES:
            _, removed = _images.popitem(last=False)
            _total_bytes -= len(removed)
    return reference, digest


def get_source_image(reference: str) -> Optional[bytes]:
    with _lock:
        data = _images.get(reference)
        if data is not None:
            _images.move_to_end(reference)
        return data

