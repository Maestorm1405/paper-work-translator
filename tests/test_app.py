import json
from types import SimpleNamespace

import anthropic
import httpx2
import pytest
from fastapi.testclient import TestClient

from paperwork_translator.app import create_app
from paperwork_translator.translator import ASK_SYSTEM_PROMPT, OUTPUT_SCHEMA, SYSTEM_PROMPT

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PDF = b"%PDF-1.7\n" + b"0" * 64

GOOD_RESULT = {
    "is_medical_document": True,
    "document_type": "Hospital discharge instructions",
    "summary": "You were in the hospital for an infection. This paper tells you how to get better at home.",
    "warning_signs": ["Call 911 if you have trouble breathing."],
    "concerns": [],
    "key_points": ["You had a bladder infection."],
    "todos": [{"task": "See your regular doctor.", "when": "November 18, 2026 at 2:30 PM", "date": "2026-11-18", "time": "14:30"}],
    "medicines": [{"name": "Nitrofurantoin 100 mg", "how_to_take": "Take 1 capsule 2 times a day for 5 days.", "notes": None}],
    "words_to_know": [{"term": "UTI", "meaning": "an infection in the parts of your body that make and store pee"}],
    "questions_for_doctor": ["Should I take this medicine with food?"],
    "unclear_parts": [],
    "bill": None,
    "original_text": "Nitrofurantoin 100mg PO BID x5d",
}


class FakeStream:
    def __init__(self, message):
        self.message = message

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self.message


class FakeClient:
    """Stands in for anthropic.Anthropic: records the request and returns a canned message or error."""

    def __init__(self, *, text=None, stop_reason="end_turn", error=None, content=None):
        self.calls = []
        if content is None:
            content = [SimpleNamespace(type="text", text=text if text is not None else json.dumps(GOOD_RESULT))]
        message = SimpleNamespace(stop_reason=stop_reason, content=content)

        def stream(**kwargs):
            self.calls.append(kwargs)
            if error is not None:
                raise error
            return FakeStream(message)

        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=stream))


def make(fake, monkeypatch, key=True, **env):
    """A test app. With key=False there is no AI key, so own papers are read on this computer."""
    for name in ("TRANSLATOR_MODEL", "TRANSLATOR_EFFORT", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    if key:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr("paperwork_translator.app.load_dotenv", lambda: None)
    return TestClient(create_app(client_factory=lambda: fake))


def status_error(cls, code):
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls("boom", response=httpx2.Response(code, request=request), body=None)


def last_text(call):
    return call["messages"][0]["content"][-1]["text"]


# ---------- Translating ----------


def test_translates_pasted_text_and_sends_expected_request(monkeypatch):
    fake = FakeClient()
    client = make(fake, monkeypatch)

    response = client.post("/api/translate", data={"text": "  Take nitrofurantoin 100mg PO BID x5d  "})

    assert response.status_code == 200
    body = response.json()
    assert body["summary"] == GOOD_RESULT["summary"]
    assert body["source"] == "ai" and body["language"] == "en" and body["reading_level"] == "simple"
    # For pasted text we keep the person's exact words instead of the model's copy.
    assert body["original_text"] == "Take nitrofurantoin 100mg PO BID x5d"
    call = fake.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["fallbacks"] == "default"
    assert call["betas"] == ["server-side-fallback-2026-07-01"]
    assert call["output_config"] == {"effort": "medium", "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}}
    assert call["system"][0]["text"] == SYSTEM_PROMPT
    assert "thinking" not in call and "temperature" not in call
    content = call["messages"][0]["content"]
    assert "<paper>\nTake nitrofurantoin 100mg PO BID x5d\n</paper>" in content[0]["text"]
    assert "Write in English" in last_text(call) and "5th grade" in last_text(call)


def test_language_and_reading_level_reach_the_prompt(monkeypatch):
    fake = FakeClient()
    client = make(fake, monkeypatch)
    response = client.post("/api/translate", data={"text": "hello", "language": "ar", "reading_level": "very_simple"})
    assert response.status_code == 200
    assert response.json()["direction"] == "rtl"
    assert response.json()["reading_grade"]["plain"] is None  # only scored in English
    instructions = last_text(fake.calls[0])
    assert "Write in Arabic" in instructions and "easy read" in instructions


@pytest.mark.parametrize("field", [{"language": "klingon"}, {"reading_level": "expert"}])
def test_rejects_unknown_language_or_level(monkeypatch, field):
    response = make(FakeClient(), monkeypatch).post("/api/translate", data={"text": "hello", **field})
    assert response.status_code == 400


def test_model_and_effort_come_from_environment(monkeypatch):
    fake = FakeClient()
    client = make(fake, monkeypatch, TRANSLATOR_MODEL="claude-sonnet-5-5", TRANSLATOR_EFFORT="low")
    client.post("/api/translate", data={"text": "hello"})
    assert fake.calls[0]["model"] == "claude-sonnet-5-5"
    assert fake.calls[0]["output_config"]["effort"] == "low"


def test_invalid_effort_falls_back_to_default(monkeypatch):
    fake = FakeClient()
    make(fake, monkeypatch, TRANSLATOR_EFFORT="turbo").post("/api/translate", data={"text": "hello"})
    assert fake.calls[0]["output_config"]["effort"] == "medium"


def test_photo_is_sent_as_image_using_detected_type(monkeypatch):
    fake = FakeClient()
    client = make(fake, monkeypatch)
    # The browser label says JPEG, but the bytes are PNG; the bytes win.
    response = client.post("/api/translate", files={"files": ("paper.jpg", PNG, "image/jpeg")})
    assert response.status_code == 200
    assert response.json()["original_text"] == GOOD_RESULT["original_text"]  # the model's transcription
    block = fake.calls[0]["messages"][0]["content"][0]
    assert block["type"] == "image"
    assert block["source"]["media_type"] == "image/png"


def test_pdf_is_sent_as_document_before_text(monkeypatch):
    fake = FakeClient()
    client = make(fake, monkeypatch)
    response = client.post("/api/translate", files={"files": ("paper.pdf", PDF, "application/pdf")}, data={"text": "page 2 is missing"})
    assert response.status_code == 200
    content = fake.calls[0]["messages"][0]["content"]
    assert content[0]["type"] == "document"
    assert content[0]["source"]["media_type"] == "application/pdf"
    assert "page 2 is missing" in content[1]["text"]


def test_several_scanned_pages_are_sent_in_order(monkeypatch):
    fake = FakeClient()
    client = make(fake, monkeypatch)
    jpeg = b"\xff\xd8\xff" + b"\x00" * 64
    pages = [("files", ("page-1.jpg", jpeg, "image/jpeg")), ("files", ("page-2.png", PNG, "image/png")),
             ("files", ("page-3.pdf", PDF, "application/pdf"))]
    response = client.post("/api/translate", files=pages)
    assert response.status_code == 200
    content = fake.calls[0]["messages"][0]["content"]
    assert [block["type"] for block in content[:3]] == ["image", "image", "document"]
    assert content[0]["source"]["media_type"] == "image/jpeg" and content[1]["source"]["media_type"] == "image/png"
    assert "These 3 files are pages of the same paper, in order." == content[3]["text"]


def test_rejects_too_many_pages(monkeypatch):
    fake = FakeClient()
    pages = [("files", (f"p{i}.png", PNG, "image/png")) for i in range(11)]
    response = make(fake, monkeypatch).post("/api/translate", files=pages)
    assert response.status_code == 413 and "10 pages" in response.json()["error"]
    assert fake.calls == []


def test_rejects_pages_that_are_too_big_together(monkeypatch):
    page = PNG + b"\x00" * (4 * 1024 * 1024)
    pages = [("files", (f"p{i}.png", page, "image/png")) for i in range(6)]
    response = make(FakeClient(), monkeypatch).post("/api/translate", files=pages)
    assert response.status_code == 413 and "together" in response.json()["error"]


def test_requires_some_input(monkeypatch):
    fake = FakeClient()
    response = make(fake, monkeypatch).post("/api/translate", data={"text": "   "})
    assert response.status_code == 400
    assert "photo or PDF" in response.json()["error"]
    assert fake.calls == []


def test_rejects_unsupported_files(monkeypatch):
    client = make(FakeClient(), monkeypatch)
    response = client.post("/api/translate", files={"files": ("notes.docx", b"PK\x03\x04 not a photo", "application/octet-stream")})
    assert response.status_code == 415


def test_rejects_oversized_images(monkeypatch):
    client = make(FakeClient(), monkeypatch)
    big = PNG + b"\x00" * (5 * 1024 * 1024)
    response = client.post("/api/translate", files={"files": ("big.png", big, "image/png")})
    assert response.status_code == 413
    assert "5 MB" in response.json()["error"]


def test_rejects_very_long_text(monkeypatch):
    response = make(FakeClient(), monkeypatch).post("/api/translate", data={"text": "a" * 100_001})
    assert response.status_code == 413


def test_bad_times_are_dropped(monkeypatch):
    result = {**GOOD_RESULT, "todos": [{"task": "Visit", "when": None, "date": "2026-11-18", "time": "2:30 PM"}]}
    response = make(FakeClient(text=json.dumps(result)), monkeypatch).post("/api/translate", data={"text": "x"})
    assert response.json()["todos"][0] == {"task": "Visit", "when": None, "date": "2026-11-18", "time": None}


@pytest.mark.parametrize(("stop_reason", "phrase"), [("refusal", "couldn't be done"), ("max_tokens", "too long")])
def test_explains_incomplete_answers(monkeypatch, stop_reason, phrase):
    client = make(FakeClient(text="{", stop_reason=stop_reason), monkeypatch)
    response = client.post("/api/translate", data={"text": "hello"})
    assert response.status_code == 422
    assert phrase in response.json()["error"]


def test_uses_last_text_block_after_a_fallback(monkeypatch):
    content = [
        SimpleNamespace(type="text", text='{"partial'),
        SimpleNamespace(type="fallback"),
        SimpleNamespace(type="text", text=json.dumps(GOOD_RESULT)),
    ]
    response = make(FakeClient(content=content), monkeypatch).post("/api/translate", data={"text": "hello"})
    assert response.status_code == 200
    assert response.json()["document_type"] == "Hospital discharge instructions"


def test_output_that_does_not_match_schema_is_an_error(monkeypatch):
    response = make(FakeClient(text='{"summary": "missing fields"}'), monkeypatch).post("/api/translate", data={"text": "hello"})
    assert response.status_code == 502


@pytest.mark.parametrize(
    ("error", "status", "phrase"),
    [
        (status_error(anthropic.RateLimitError, 429), 503, "busy"),
        (status_error(anthropic.OverloadedError, 529), 503, "busy"),
        (status_error(anthropic.AuthenticationError, 401), 503, "API key"),
        (status_error(anthropic.BadRequestError, 400), 422, "clearer photo"),
        (status_error(anthropic.InternalServerError, 500), 502, "try again"),
        (anthropic.APITimeoutError(request=httpx2.Request("POST", "https://api.anthropic.com")), 504, "too long"),
        (anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com")), 503, "internet"),
        (TypeError("Could not resolve authentication method. Expected one of api_key"), 503, "API key"),
    ],
)
def test_api_errors_become_friendly_messages(monkeypatch, error, status, phrase):
    response = make(FakeClient(error=error), monkeypatch).post("/api/translate", data={"text": "hello"})
    assert response.status_code == status
    assert phrase in response.json()["error"]


# ---------- Sample papers ----------


def test_samples_work_without_an_api_key(monkeypatch):
    fake = FakeClient()
    client = make(fake, monkeypatch, key=False)
    for level in ("simple", "very_simple"):
        body = client.post("/api/translate", data={"sample_id": "discharge", "reading_level": level}).json()
        assert body["source"] == "sample" and body["reading_level"] == level
        assert body["sample"]["suggested_questions"]
        assert "RIVERSIDE GENERAL HOSPITAL" in body["original_text"]
    assert fake.calls == []


def test_sample_in_other_language_without_key_falls_back_to_english(monkeypatch):
    fake = FakeClient()
    body = make(fake, monkeypatch, key=False).post("/api/translate", data={"sample_id": "denial", "language": "es"}).json()
    assert body["language"] == "en"
    assert "isn't set up" in body["notice"]
    assert fake.calls == []


def test_sample_in_other_language_with_key_is_translated_live(monkeypatch):
    fake = FakeClient()
    client = make(fake, monkeypatch, ANTHROPIC_API_KEY="sk-test")
    body = client.post("/api/translate", data={"sample_id": "bill", "language": "es"}).json()
    assert body["source"] == "ai" and body["language"] == "es"
    assert body["sample"]["id"] == "bill"
    assert "PATIENT STATEMENT" in fake.calls[0]["messages"][0]["content"][0]["text"]


def test_unknown_sample(monkeypatch):
    assert make(FakeClient(), monkeypatch).post("/api/translate", data={"sample_id": "nope"}).status_code == 404


def test_sample_bill_problems_are_found_by_the_math_check(monkeypatch):
    body = make(FakeClient(), monkeypatch).post("/api/translate", data={"sample_id": "bill"}).json()
    math = [c for c in body["concerns"] if c["source"] == "math_check"]
    assert len(math) == 3
    assert "$4,340.00" in math[0]["issue"] and "$4,430.00" in math[0]["issue"]
    assert "$570.00" in math[1]["issue"] and "$660.00" in math[1]["issue"]
    assert "listed 2 times" in math[2]["issue"]
    assert body["bill_check"]["total_matches"] is False
    assert body["concerns"][0]["source"] == "math_check"  # the app's own findings come first


def test_samples_score_lower_reading_level_than_the_original(monkeypatch):
    client = make(FakeClient(), monkeypatch)
    for sample in ("discharge", "bill", "denial"):
        grades = client.post("/api/translate", data={"sample_id": sample}).json()["reading_grade"]
        assert grades["plain"] < grades["original"]


def test_config_lists_languages_levels_and_samples(monkeypatch):
    config = make(FakeClient(), monkeypatch, key=False).get("/api/config").json()
    assert config["api_key_configured"] is False
    assert {"code": "ar", "name": "Arabic", "native_name": "العربية", "rtl": True} in config["languages"]
    assert [lvl["code"] for lvl in config["reading_levels"]] == ["simple", "very_simple"]
    assert [s["id"] for s in config["samples"]] == ["discharge", "bill", "denial"]
    assert make(FakeClient(), monkeypatch, ANTHROPIC_API_KEY="sk").get("/api/config").json()["api_key_configured"] is True


# ---------- Questions ----------


def ask_body(**overrides):
    body = {"question": "Can I drink alcohol?", "paper_text": "Take with food.", "translation": dict(GOOD_RESULT),
            "history": [], "language": "en", "reading_level": "simple"}
    return {**body, **overrides}


def test_suggested_sample_questions_are_answered_without_a_key(monkeypatch):
    fake = FakeClient()
    body = ask_body(question="  how much do I really owe ", sample_id="bill")
    response = make(fake, monkeypatch, key=False).post("/api/ask", json=body)
    assert response.status_code == 200
    assert response.json()["source"] == "sample"
    assert "$570.00" in response.json()["answer"]
    assert fake.calls == []


def test_other_questions_need_the_service(monkeypatch):
    response = make(FakeClient(), monkeypatch, key=False).post("/api/ask", json=ask_body(sample_id="bill"))
    assert response.status_code == 503
    assert "sample" in response.json()["error"]


def test_questions_are_answered_from_the_paper(monkeypatch):
    fake = FakeClient(text="  The paper doesn't say. Ask your pharmacist.  ")
    client = make(fake, monkeypatch, ANTHROPIC_API_KEY="sk-test")
    history = [{"question": "What is this?", "answer": "A discharge paper."}]
    response = client.post("/api/ask", json=ask_body(history=history, language="es"))
    assert response.json() == {"answer": "The paper doesn't say. Ask your pharmacist.", "source": "ai"}
    call = fake.calls[0]
    assert call["system"][0]["text"] == ASK_SYSTEM_PROMPT
    assert "format" not in call["output_config"]
    prompt = call["messages"][0]["content"][0]["text"]
    assert "<paper>\nTake with food.\n</paper>" in prompt
    assert "Question: What is this?\nAnswer: A discharge paper." in prompt
    assert "Write in Spanish" in prompt
    assert prompt.rstrip().endswith("<question>\nCan I drink alcohol?\n</question>")
    assert '"original_text"' not in prompt  # not sent twice


@pytest.mark.parametrize(
    ("overrides", "status"),
    [({"question": "  "}, 400), ({"question": "x" * 1001}, 413), ({"language": "xx"}, 400), ({"paper_text": "a" * 100_001}, 413)],
)
def test_ask_validates_input(monkeypatch, overrides, status):
    client = make(FakeClient(), monkeypatch, ANTHROPIC_API_KEY="sk-test")
    assert client.post("/api/ask", json=ask_body(**overrides)).status_code == status


def test_ask_errors_become_friendly_messages(monkeypatch):
    client = make(FakeClient(error=status_error(anthropic.RateLimitError, 429)), monkeypatch, ANTHROPIC_API_KEY="sk")
    response = client.post("/api/ask", json=ask_body())
    assert response.status_code == 503 and "busy" in response.json()["error"]


# ---------- Page and schema ----------


def test_serves_the_page(monkeypatch):
    client = make(FakeClient(), monkeypatch)
    page = client.get("/")
    assert page.status_code == 200
    assert "Medical Paperwork Translator" in page.text and "Read everything aloud" in page.text
    for asset in ("app.js", "speech.js", "calendar.js", "settings.js", "styles.css"):
        assert client.get(f"/{asset}").status_code == 200


def test_schema_is_api_compatible():
    def check(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node.get("additionalProperties") is False
                assert set(node["required"]) == set(node["properties"])
            for value in node.values():
                check(value)
        elif isinstance(node, list):
            for value in node:
                check(value)

    check(OUTPUT_SCHEMA)


# ---------- Reading without an AI key ----------


def test_pasted_bill_is_read_and_checked_without_a_key(monkeypatch):
    fake = FakeClient()
    paper = json.loads(open("paperwork_translator/samples/bill.json").read())["paper_text"]
    body = make(fake, monkeypatch, key=False).post("/api/translate", data={"text": paper}).json()
    assert body["source"] == "local" and "without AI" in body["notice"]
    math = [c["issue"] for c in body["concerns"] if c["source"] == "math_check"]
    assert len(math) == 3 and "$4,340.00" in math[0] and "$570.00" in math[1]
    assert body["todos"][0]["date"] == "2026-11-14"
    assert fake.calls == []


def test_discharge_paper_without_a_key_explains_abbreviations_and_finds_conflicts(monkeypatch):
    paper = json.loads(open("paperwork_translator/samples/discharge.json").read())["paper_text"]
    body = make(FakeClient(), monkeypatch, key=False).post("/api/translate", data={"text": paper, "language": "es"}).json()
    assert body["language"] == "en" and "Other languages" in body["notice"]
    assert "By mouth 2 times a day for 5 days" in body["medicines"][0]["how_to_take"]
    assert any("5 days and 7 days" in c["issue"] for c in body["concerns"])
    assert {"2026-11-18"} <= {t["date"] for t in body["todos"]} and any(t["time"] == "14:30" for t in body["todos"])
    assert any("911" in w for w in body["warning_signs"])


def test_photos_without_a_key_must_be_read_by_the_page(monkeypatch):
    response = make(FakeClient(), monkeypatch, key=False).post("/api/translate", files={"files": ("p.png", PNG, "image/png")})
    assert response.status_code == 422


def test_pdf_without_text_explains_what_to_do(monkeypatch):
    response = make(FakeClient(), monkeypatch, key=False).post("/api/translate", files={"files": ("p.pdf", PDF, "application/pdf")})
    assert response.status_code == 422 and "photos" in response.json()["error"]
