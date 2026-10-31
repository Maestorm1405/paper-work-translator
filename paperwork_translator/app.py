"""Web server: serves the page, translates papers, and answers questions about them."""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Callable

import anthropic
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .checks import check_bill, plain_text, reading_grade
from .translator import (
    DEFAULT_EFFORT,
    DEFAULT_MODEL,
    EFFORT_LEVELS,
    IMAGE_TYPES,
    LANGUAGES,
    PDF_TYPE,
    READING_LEVELS,
    RTL_LANGUAGES,
    Translation,
    TranslationError,
    answer_question,
    sniff_media_type,
    translate,
)

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
SAMPLES_DIR = Path(__file__).parent / "samples"
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_PDF_BYTES = 20 * 1024 * 1024
MAX_FILES = 10
MAX_TOTAL_BYTES = 20 * 1024 * 1024  # stays under Claude's request size once encoded
MAX_TEXT_CHARS = 100_000
MAX_QUESTION_CHARS = 1_000
MAX_HISTORY = 10

NEEDS_SERVICE = (
    "This needs the translation service, which isn't set up yet. "
    "You can still try the sample papers and their suggested questions."
)


def _error(message: str, status_code: int) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status_code)


def read_uploads(uploads: list[UploadFile]) -> list[tuple[bytes, str]] | JSONResponse:
    """Check uploaded photos/PDFs and return (bytes, media type) pairs, or an error response."""
    uploads = [u for u in uploads if u is not None and u.filename]
    if len(uploads) > MAX_FILES:
        return _error(f"Please add {MAX_FILES} pages or fewer at a time.", 413)
    files, total = [], 0
    for upload in uploads:
        data = upload.file.read(MAX_PDF_BYTES + 1)
        if not data:
            continue
        media_type = sniff_media_type(data)
        if media_type not in (*IMAGE_TYPES, PDF_TYPE):
            return _error("Please choose photos (JPG, PNG, WebP, or GIF) or a PDF file.", 415)
        limit = MAX_PDF_BYTES if media_type == PDF_TYPE else MAX_IMAGE_BYTES
        if len(data) > limit:
            size = "20 MB" if media_type == PDF_TYPE else "5 MB"
            return _error(f"One of the files is too big. Please use files smaller than {size}.", 413)
        total += len(data)
        if total > MAX_TOTAL_BYTES:
            return _error("Those files are too big together. Please send fewer pages at a time.", 413)
        files.append((data, media_type))
    return files


def _normalize_question(q: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", "", q.lower()).split())


def load_samples(directory: Path = SAMPLES_DIR) -> dict[str, dict[str, Any]]:
    samples = {}
    for path in sorted(directory.glob("*.json")):
        sample = json.loads(path.read_text(encoding="utf-8"))
        for level, result in sample["results"].items():
            # Validate at startup so a broken sample fails loudly, not during a demo.
            full = Translation.model_validate({**result, "original_text": sample["paper_text"]})
            sample["results"][level] = full.model_dump(mode="json")
        samples[sample["id"]] = sample
    return dict(sorted(samples.items(), key=lambda item: item[1].get("order", 0)))


def finish(result: dict[str, Any], *, language: str, reading_level: str, source: str, notice: str | None = None,
           sample: dict[str, Any] | None = None) -> dict[str, Any]:
    """Add the app's own checks and display details to a translation."""
    result = json.loads(json.dumps(result))  # deep copy so canned samples are never modified
    for todo in result["todos"]:
        if todo.get("time") and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", todo["time"]):
            todo["time"] = None

    bill_check, bill_concerns = check_bill(result.get("bill"))
    for concern in bill_concerns:
        concern["source"] = "math_check"
    for concern in result["concerns"]:
        concern["source"] = "ai"
    result["concerns"] = bill_concerns + result["concerns"]
    result["bill_check"] = bill_check

    english = language == "en"
    result["reading_grade"] = {
        "original": reading_grade(result["original_text"]),
        "plain": reading_grade(plain_text(result), min_words=20) if english else None,
    }
    result.update(
        language=language,
        direction="rtl" if language in RTL_LANGUAGES else "ltr",
        reading_level=reading_level,
        source=source,
        notice=notice,
        sample=None if sample is None else {
            "id": sample["id"],
            "title": sample["title"],
            "suggested_questions": [q["question"] for q in sample.get("questions", [])],
        },
    )
    return result


class PastQuestion(BaseModel):
    question: str
    answer: str


class AskRequest(BaseModel):
    question: str
    paper_text: str
    translation: dict[str, Any]
    history: list[PastQuestion] = []
    language: str = "en"
    reading_level: str = "simple"
    sample_id: str | None = None


def create_app(client_factory: Callable[[], anthropic.Anthropic] | None = None) -> FastAPI:
    load_dotenv()
    model = os.environ.get("TRANSLATOR_MODEL", DEFAULT_MODEL)
    effort = os.environ.get("TRANSLATOR_EFFORT", DEFAULT_EFFORT)
    if effort not in EFFORT_LEVELS:
        log.warning("Ignoring TRANSLATOR_EFFORT=%r; expected one of %s", effort, EFFORT_LEVELS)
        effort = DEFAULT_EFFORT
    make_client = client_factory or (lambda: anthropic.Anthropic(timeout=180.0, max_retries=2))
    samples = load_samples()

    def key_configured() -> bool:
        return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))

    app = FastAPI(title="Medical Paperwork Translator")

    @app.get("/api/config")
    def config() -> dict:
        return {
            "api_key_configured": key_configured(),
            "model": model,
            "max_files": MAX_FILES,
            "languages": [
                {"code": code, "name": name, "native_name": native, "rtl": code in RTL_LANGUAGES}
                for code, (name, native) in LANGUAGES.items()
            ],
            "reading_levels": [{"code": code, "name": name} for code, (name, _) in READING_LEVELS.items()],
            "samples": [{"id": s["id"], "title": s["title"], "description": s["description"]} for s in samples.values()],
        }

    # Plain `def` endpoints run in FastAPI's thread pool, so the blocking Claude calls are fine here.
    @app.post("/api/translate")
    def translate_paper(
        text: str | None = Form(None),
        files: list[UploadFile] = File(default_factory=list),
        sample_id: str | None = Form(None),
        language: str = Form("en"),
        reading_level: str = Form("simple"),
    ):
        if language not in LANGUAGES:
            return _error("Please choose one of the languages in the list.", 400)
        if reading_level not in READING_LEVELS:
            return _error("Please choose Simple or Very simple.", 400)
        style = {"language": language, "reading_level": reading_level}

        uploads: list[tuple[bytes, str]] = []
        if sample_id:
            sample = samples.get(sample_id)
            if sample is None:
                return _error("That sample paper wasn't found.", 404)
            canned = sample["results"][reading_level]
            if language == "en":
                return finish(canned, source="sample", sample=sample, **style)
            if not key_configured():
                notice = "Other languages need the translation service, which isn't set up yet, so this is in English."
                return finish(canned, source="sample", sample=sample, notice=notice,
                              language="en", reading_level=reading_level)
            text = sample["paper_text"]
        else:
            sample = None
            uploads_or_error = read_uploads(files)
            if isinstance(uploads_or_error, JSONResponse):
                return uploads_or_error
            uploads = uploads_or_error

        text = (text or "").strip() or None
        if text and len(text) > MAX_TEXT_CHARS:
            return _error("That's a lot of text. Please paste one or two pages at a time.", 413)
        if not uploads and text is None:
            return _error("Please add a photo or PDF of your paper, or paste its words.", 400)

        try:
            result = translate(make_client(), text=text, files=uploads, model=model, effort=effort, **style)
        except TranslationError as exc:
            return _error(exc.message, exc.status_code)
        result = result.model_dump(mode="json")
        if not uploads:
            result["original_text"] = text  # we already have the exact words; don't rely on a copy
        return finish(result, source="ai", sample=sample, **style)

    @app.post("/api/ask")
    def ask(request: AskRequest):
        question = request.question.strip()
        if not question:
            return _error("Please type or say a question first.", 400)
        if len(question) > MAX_QUESTION_CHARS:
            return _error("Please ask a shorter question.", 413)
        if len(request.paper_text) > MAX_TEXT_CHARS:
            return _error("That paper is too long to ask questions about.", 413)
        if request.language not in LANGUAGES or request.reading_level not in READING_LEVELS:
            return _error("Please choose a language and reading level from the lists.", 400)

        sample = samples.get(request.sample_id or "")
        if sample and request.language == "en":
            wanted = _normalize_question(question)
            for qa in sample.get("questions", []):
                if _normalize_question(qa["question"]) == wanted:
                    return {"answer": qa["answer"], "source": "sample"}

        if not key_configured():
            return _error(NEEDS_SERVICE, 503)

        translation = {k: v for k, v in request.translation.items() if k not in ("original_text", "sample", "notice")}
        history = [(h.question, h.answer) for h in request.history[-MAX_HISTORY:]]
        try:
            answer = answer_question(
                make_client(), question=question, paper_text=request.paper_text, translation=translation,
                history=history, language=request.language, reading_level=request.reading_level,
                model=model, effort=effort,
            )
        except TranslationError as exc:
            return _error(exc.message, exc.status_code)
        return {"answer": answer, "source": "ai"}

    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app


app = create_app()
