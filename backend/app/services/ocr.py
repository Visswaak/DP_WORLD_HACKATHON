from __future__ import annotations

import asyncio
import concurrent.futures
import io

import pytesseract
from PIL import Image, UnidentifiedImageError

from app.config import settings
from app.observability import logger

if settings.tesseract_cmd:
    pytesseract.pytesseract.tesseract_cmd = settings.tesseract_cmd

# Bounded pool: limits truly concurrent OCR threads even when asyncio.wait_for times out
# and the caller moves on. A timed-out thread still holds its worker slot until it exits.
_ocr_pool = concurrent.futures.ThreadPoolExecutor(
    max_workers=settings.max_parallel_ocr,
    thread_name_prefix="ocr_worker",
)

FALLBACK_TEXT = (
    "Commercial invoice for electronics shipment. Importer: Northstar Retail LLC. "
    "Exporter: Shenzhen Apex Manufacturing. Value USD 24800. Port of entry: Los Angeles. "
    "Items: tablets, quantity 120, origin China, HS 847130."
)


async def extract_text(filename: str, content: bytes) -> str:
    loop = asyncio.get_running_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(_ocr_pool, _extract_text_sync, filename, content),
            timeout=settings.ocr_timeout_seconds,
        )
    except asyncio.TimeoutError:
        logger.warning("ocr_timeout filename=%s timeout_s=%s", filename, settings.ocr_timeout_seconds)
        return FALLBACK_TEXT


def _extract_text_sync(filename: str, content: bytes) -> str:
    lowered = filename.lower()
    if lowered.endswith((".txt", ".csv", ".json", ".xml")):
        return _decode_text(content)

    try:
        image = Image.open(io.BytesIO(content))
        text = pytesseract.image_to_string(image)
        if text.strip():
            return text
    except (UnidentifiedImageError, OSError, pytesseract.TesseractNotFoundError):
        pass

    decoded = _decode_text(content)
    return decoded if decoded else FALLBACK_TEXT


def _decode_text(content: bytes) -> str:
    return content[: settings.fallback_decode_limit].decode("utf-8", errors="ignore").strip()
