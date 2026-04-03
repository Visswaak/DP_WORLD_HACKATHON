from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from openai import OpenAI

from app.config import settings
from app.observability import logger
from app.schemas import ExtractedShipmentData, ShipmentItem

CLASSIFICATION_RULES = (
    ("Bill of Lading", ("bill of lading", "bol")),
    ("Commercial Invoice", ("invoice",)),
    ("Packing List", ("packing",)),
    ("Certificate of Origin", ("certificate",)),
)
IMPORTER_PATTERN = re.compile(r"Importer[:\s]+([A-Za-z0-9 &.,-]+)", re.IGNORECASE)
EXPORTER_PATTERN = re.compile(r"Exporter[:\s]+([A-Za-z0-9 &.,-]+)", re.IGNORECASE)
PORT_PATTERN = re.compile(r"Port of entry[:\s]+([A-Za-z ]+)", re.IGNORECASE)
VALUE_PATTERN = re.compile(r"(?:USD|Value USD)\s*([0-9]+(?:\.[0-9]+)?)", re.IGNORECASE)
QUANTITY_PATTERN = re.compile(r"quantity[:\s]+([0-9]+)", re.IGNORECASE)
HS_PATTERN = re.compile(r"HS[:\s]+([0-9]{6,12})", re.IGNORECASE)
JSON_PATTERN = re.compile(r"\{.*\}", re.DOTALL)
OPENAI_SEMAPHORE = asyncio.Semaphore(settings.max_parallel_llm)

# Strip only truly dangerous control characters; preserve \n (0x0a), \r (0x0d), \t (0x09)
# so invoice table structure survives into the prompt.
_PROMPT_UNSAFE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _sanitize_for_prompt(value: str) -> str:
    """Strip dangerous control characters while preserving newlines and tabs."""
    return _PROMPT_UNSAFE.sub(" ", value)


def classify_document(filename: str, text: str) -> str:
    # Text body takes priority over filename; rules ordered so more specific patterns win
    text_lower = text.lower()
    for label, keywords in CLASSIFICATION_RULES:
        if any(keyword in text_lower for keyword in keywords):
            return label
    filename_lower = filename.lower()
    for label, keywords in CLASSIFICATION_RULES:
        if any(keyword in filename_lower for keyword in keywords):
            return label
    return "Shipping Document"


async def extract_structured_data(filename: str, text: str) -> tuple[ExtractedShipmentData, dict[str, Any]]:
    if settings.active_api_key:
        try:
            async with OPENAI_SEMAPHORE:
                return await _extract_with_openai(filename, text)
        except asyncio.TimeoutError:
            logger.warning("llm_timeout filename=%s timeout_s=%s", filename, settings.api_timeout_seconds)
            return _extract_with_fallback(text), {"mode": "fallback", "error": "TimeoutError"}
        except Exception as exc:
            logger.warning("llm_error filename=%s error=%s: %s", filename, type(exc).__name__, exc)
            return _extract_with_fallback(text), {"mode": "fallback", "error": type(exc).__name__}
    return _extract_with_fallback(text), {"mode": "fallback"}


async def _extract_with_openai(filename: str, text: str) -> tuple[ExtractedShipmentData, dict[str, Any]]:
    client = _get_openai_client()
    safe_filename = _sanitize_for_prompt(filename)
    safe_text = _sanitize_for_prompt(text[: settings.model_text_limit])
    prompt = (
        "Extract structured shipment data from the document below and return valid JSON with these keys:\n"
        "  importer, exporter, incoterm, port_of_entry, shipment_value_usd, currency,\n"
        "  items (array of: description, quantity, country_of_origin, hs_code, declared_value_usd)\n\n"
        "Field extraction rules:\n"
        "- exporter: full company name from 'Name of Exporter / Manufacturer' or 'Seller' section — NOT a reference number\n"
        "- importer: buyer/consignee company name; if consignee is 'To The Order Of' with no name, use the Notify Party company name\n"
        "- incoterm: delivery terms code (CIF, FOB, EXW, etc.) from 'Terms of Delivery & Payment' or 'Incoterm' field — IGNORE line items labelled 'FOB Value' or 'CIF Value' which are subtotals, not the incoterm\n"
        "- port_of_entry: destination port from 'Port of Discharge' or 'Port of Destination' or 'Final Destination' field\n"
        "- shipment_value_usd: the grand total invoice amount in USD — use 'Total CIF Value', 'Total Amount in USD', or the sum of all line item amounts; NOT subtotals like FOB Value or freight alone\n"
        "- items[].quantity: integer number of units\n"
        "- items[].hs_code: HS or ITC-HS code as a string (e.g. '10063012')\n"
        "- items[].declared_value_usd: line-item amount in USD\n\n"
        "Respond with valid JSON only.\n"
        f"Filename: {safe_filename}\nDocument text:\n{safe_text}"
    )
    response = await asyncio.wait_for(
        asyncio.to_thread(
            client.chat.completions.create,
            model=settings.openai_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            response_format={"type": "json_object"},
        ),
        timeout=settings.api_timeout_seconds,
    )
    content = response.choices[0].message.content or ""
    try:
        payload = json.loads(_extract_json_block(content))
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not parse JSON from model output: {exc}") from exc
    return ExtractedShipmentData.model_validate(payload), {
        "mode": "openai",
        "response_excerpt": content[:1000],
    }


def _extract_json_block(text: str) -> str:
    match = JSON_PATTERN.search(text)
    if not match:
        raise ValueError("No JSON object found in model output")
    return match.group(0)


def _extract_with_fallback(text: str) -> ExtractedShipmentData:
    lowered = text.lower()
    importer = _match(IMPORTER_PATTERN, text, "Unknown")
    exporter = _match(EXPORTER_PATTERN, text, "Unknown")
    port = _match(PORT_PATTERN, text, "Unknown")
    incoterm = "FOB" if "fob" in lowered else "CIF" if "cif" in lowered else "Unknown"
    value = float(_match(VALUE_PATTERN, text, "0"))
    quantity = int(_match(QUANTITY_PATTERN, text, "0"))
    origin = "China" if "china" in lowered else "Vietnam" if "vietnam" in lowered else "Unknown"
    hs_code = _match(HS_PATTERN, text, "000000")
    item_name = "Tablet Computers" if "tablet" in lowered else "Unknown Item"

    return ExtractedShipmentData(
        importer=importer.strip(" ."),
        exporter=exporter.strip(" ."),
        incoterm=incoterm,
        port_of_entry=port.strip(" ."),
        shipment_value_usd=value,
        currency="USD",
        items=[
            ShipmentItem(
                description=item_name,
                quantity=quantity,
                country_of_origin=origin,
                hs_code=hs_code,
                declared_value_usd=value,
            )
        ],
    )


def _match(pattern: re.Pattern[str], text: str, default: str) -> str:
    match = pattern.search(text)
    return match.group(1) if match else default


_openai_client: OpenAI | None = None


def _get_openai_client() -> OpenAI:
    global _openai_client
    if _openai_client is None:
        _openai_client = OpenAI(
            api_key=settings.active_api_key,
            base_url=settings.active_api_base_url,
        )
    return _openai_client
