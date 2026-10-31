import os

import pytest

from paperwork_translator import desktop


@pytest.fixture
def app_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(desktop, "data_dir", lambda: tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return tmp_path


def test_api_key_is_saved_privately_and_loaded_next_time(app_folder):
    bridge = desktop.DesktopBridge()
    assert bridge.api_key_status() == {"configured": False}
    assert bridge.save_api_key("  sk-ant-test-123  ") == {"ok": True, "configured": True}
    key_file = app_folder / desktop.KEY_FILE
    assert key_file.read_text() == "sk-ant-test-123"
    if os.name != "nt":
        assert key_file.stat().st_mode & 0o777 == 0o600

    os.environ.pop("ANTHROPIC_API_KEY")
    desktop.load_saved_key()
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-test-123"

    assert bridge.save_api_key("") == {"ok": True, "configured": False}
    assert not key_file.exists() and "ANTHROPIC_API_KEY" not in os.environ


def test_rejects_things_that_are_not_keys(app_folder):
    result = desktop.DesktopBridge().save_api_key("my password")
    assert result["ok"] is False and "sk-" in result["error"]
    assert not (app_folder / desktop.KEY_FILE).exists()


def test_calendar_event_opens_in_the_calendar_app(app_folder, monkeypatch):
    opened = []
    monkeypatch.setattr(desktop, "open_with_default_app", opened.append)
    ics = "BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n"
    result = desktop.DesktopBridge().add_to_calendar(ics, "../../urology visit.ics")
    assert result["ok"] is True
    path = opened[0]
    assert path.parent == app_folder / "calendar" and path.name == "urology visit.ics"  # no escaping the folder
    assert path.read_bytes() == ics.encode()


def test_calendar_needs_a_real_event(app_folder, monkeypatch):
    monkeypatch.setattr(desktop, "open_with_default_app", lambda p: pytest.fail("should not open"))
    assert desktop.DesktopBridge().add_to_calendar("rm -rf /", "x.ics")["ok"] is False


def test_calendar_app_missing_is_explained(app_folder, monkeypatch):
    def broken(path):
        raise OSError("no app")

    monkeypatch.setattr(desktop, "open_with_default_app", broken)
    result = desktop.DesktopBridge().add_to_calendar("BEGIN:VCALENDAR\r\n", "a.ics")
    assert result["ok"] is False and "calendar app" in result["error"]


def test_only_web_links_are_opened(monkeypatch):
    opened = []
    monkeypatch.setattr(desktop.webbrowser, "open", opened.append)
    bridge = desktop.DesktopBridge()
    assert bridge.open_url("https://calendar.google.com/calendar/render?x=1") == {"ok": True}
    assert bridge.open_url("file:///etc/passwd") == {"ok": False}
    assert bridge.open_url("javascript:alert(1)") == {"ok": False}
    assert opened == ["https://calendar.google.com/calendar/render?x=1"]


def test_safe_filename():
    assert desktop.safe_filename("my/../../summary?.txt", "x") == "my....summary.txt"
    assert desktop.safe_filename("   ", "fallback.txt") == "fallback.txt"


def test_built_in_server_passes_the_self_test(app_folder):
    server = desktop.Server()
    server.start()
    try:
        assert server.url.startswith("http://127.0.0.1:")
        assert desktop.check_server(server.url) == []
    finally:
        server.stop()
    assert not server.thread.is_alive()
