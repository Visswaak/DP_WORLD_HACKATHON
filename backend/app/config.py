from __future__ import annotations

import os


def _get_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value and value.isdigit() else default


class Settings:
    app_name = "Customs Clearance Copilot API"
    app_version = "0.2.0"
    database_url = os.getenv("DATABASE_URL")
    openai_api_key = os.getenv("OPENAI_API_KEY")
    gemini_api_key = os.getenv("GEMINI_API_KEY")
    groq_api_key = os.getenv("GROQ_API_KEY")
    # Provider priority: Groq > Gemini > OpenAI
    active_api_key: str | None = (
        os.getenv("GROQ_API_KEY")
        or os.getenv("GEMINI_API_KEY")
        or os.getenv("OPENAI_API_KEY")
    )
    active_api_base_url: str | None = (
        "https://api.groq.com/openai/v1" if os.getenv("GROQ_API_KEY")
        else "https://generativelanguage.googleapis.com/v1beta/openai/" if os.getenv("GEMINI_API_KEY")
        else None
    )
    openai_model = os.getenv(
        "OPENAI_MODEL",
        "llama-3.3-70b-versatile" if os.getenv("GROQ_API_KEY")
        else "gemini-2.0-flash" if os.getenv("GEMINI_API_KEY")
        else "gpt-4.1-mini",
    )
    tesseract_cmd = os.getenv("TESSERACT_CMD")
    api_timeout_seconds = _get_int("API_TIMEOUT_SECONDS", 20)
    max_files_per_request = _get_int("MAX_FILES_PER_REQUEST", 8)
    max_file_size_bytes = _get_int("MAX_FILE_SIZE_BYTES", 10 * 1024 * 1024)
    max_parallel_documents = _get_int("MAX_PARALLEL_DOCUMENTS", 4)
    max_parallel_ocr = _get_int("MAX_PARALLEL_OCR", 2)
    max_parallel_llm = _get_int("MAX_PARALLEL_LLM", 4)
    model_text_limit = _get_int("MODEL_TEXT_LIMIT", 12_000)
    extracted_preview_length = _get_int("EXTRACTED_PREVIEW_LENGTH", 500)
    fallback_decode_limit = _get_int("FALLBACK_DECODE_LIMIT", 64 * 1024)
    ocr_timeout_seconds = _get_int("OCR_TIMEOUT_SECONDS", 30)
    rag_top_k = _get_int("RAG_TOP_K", 5)
    embedding_model = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
    grounded_summary_max_tokens = _get_int("GROUNDED_SUMMARY_MAX_TOKENS", 350)
    usd_to_inr: float = float(os.getenv("USD_TO_INR", "84.0"))
    allowed_origins = [
        o.strip()
        for o in os.getenv("ALLOWED_ORIGINS", "http://localhost:5173").split(",")
        if o.strip()
    ]


settings = Settings()
