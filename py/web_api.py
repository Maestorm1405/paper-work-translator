"""The web version's "server": runs in the browser (Pyodide) and answers the page's /api requests with the same
Python code as the app, without AI. Photos are read by the page itself; PDFs are read here from their text."""

import json
import sys
import types

sys.path.insert(0, "/app")


def _stub(name, **attrs):
    module = types.ModuleType(name)
    module.__dict__.update(attrs)
    sys.modules[name] = module


class _Anything:
    """Stands in for the web server: accepts any setup call, and its route decorators leave functions alone."""

    def __init__(self, *args, **kwargs):
        pass

    def __call__(self, *args, **kwargs):
        return lambda function: function

    def __getattr__(self, name):
        return _Anything()


# The AI client and web server aren't needed (or available) in the browser.
_stub("anthropic", transform_schema=lambda model: {}, Anthropic=_Anything)
_stub("fastapi", FastAPI=_Anything, File=_Anything, Form=_Anything, UploadFile=_Anything)
_stub("fastapi.responses", JSONResponse=type("JSONResponse", (), {}))
_stub("fastapi.staticfiles", StaticFiles=_Anything)
_stub("dotenv", load_dotenv=lambda *args, **kwargs: None)

from paperwork_translator import app as server  # noqa: E402
from paperwork_translator.local_reader import explain, pdf_text  # noqa: E402
from paperwork_translator.translator import LANGUAGES, READING_LEVELS, RTL_LANGUAGES  # noqa: E402

SAMPLES = server.load_samples()
ENGLISH_ONLY = " Other languages need the downloadable app with an AI key, so this is in English."


def _reply(status, body):
    return [status, json.dumps(body)]


def _error(message, status):
    return _reply(status, {"error": message})


def web_config():
    return _reply(200, {
        "api_key_configured": False,
        "model": None,
        "max_files": server.MAX_FILES,
        "languages": [{"code": code, "name": name, "native_name": native, "rtl": code in RTL_LANGUAGES}
                      for code, (name, native) in LANGUAGES.items()],
        "reading_levels": [{"code": code, "name": name} for code, (name, _) in READING_LEVELS.items()],
        "samples": [{"id": s["id"], "title": s["title"], "description": s["description"]} for s in SAMPLES.values()],
    })


def web_translate(text, pdfs, sample_id, language, reading_level):
    if language not in LANGUAGES or reading_level not in READING_LEVELS:
        return _error("Please choose a language and reading level from the lists.", 400)
    if sample_id:
        sample = SAMPLES.get(sample_id)
        if sample is None:
            return _error("That sample paper wasn't found.", 404)
        notice = None if language == "en" else ENGLISH_ONLY.strip()
        return _reply(200, server.finish(sample["results"][reading_level], source="sample", sample=sample,
                                         notice=notice, language="en", reading_level=reading_level))
    try:
        words = "\n\n".join([text, *(pdf_text(pdf.to_bytes()) for pdf in pdfs)]).strip()
    except Exception:
        words = text.strip()
    if len(words.split()) < 3:
        return _error("We couldn't find words in this. If it is a scanned PDF, take photos of the pages instead, "
                      "or paste the words.", 422)
    notice = ("Read in your browser without AI, so it is simpler than a full translation. "
              "Check the original paper for anything that matters.")
    if language != "en":
        notice += ENGLISH_ONLY
    return _reply(200, server.finish(explain(words[:server.MAX_TEXT_CHARS], reading_level), source="local",
                                     notice=notice, language="en", reading_level=reading_level))


def web_ask(body):
    request = json.loads(body)
    sample = SAMPLES.get(request.get("sample_id") or "")
    if sample and request.get("language") == "en":
        wanted = server._normalize_question(request.get("question", ""))
        for qa in sample.get("questions", []):
            if server._normalize_question(qa["question"]) == wanted:
                return _reply(200, {"answer": qa["answer"], "source": "sample"})
    return _error("New questions need the downloadable app with an AI key. "
                  "For the sample papers, try the suggested questions.", 503)
