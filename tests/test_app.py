import re
import sqlite3
from types import SimpleNamespace

import pytest

import io

import summarize as summarizer
import transcribe as transcriber
from app import create_app

PASSWORD = "a long test passphrase"


def csrf_of(client):
    with client.session_transaction() as s:
        return s.get("csrf", "")


def post(client, url, **data):
    client.get("/login")  # make sure a CSRF token exists
    return client.post(url, data={"csrf": csrf_of(client), **data}, follow_redirects=True)


@pytest.fixture
def setup(tmp_path):
    calls = []

    def fake_summarize(entries):
        calls.append(entries)
        return "## Overview\nA fake summary."

    db = tmp_path / "journal.db"
    def fake_transcribe(audio):
        calls.append(audio)
        return "I talked about my week"

    app = create_app(str(db), summarize_fn=fake_summarize, transcribe_fn=fake_transcribe)
    client = app.test_client()
    post(client, "/setup", password=PASSWORD, confirm=PASSWORD)
    return SimpleNamespace(app=app, client=client, db=db, calls=calls)


def test_requires_login(tmp_path):
    client = create_app(str(tmp_path / "j.db")).test_client()
    assert client.get("/").headers["Location"].endswith("/setup")


def test_short_password_rejected(tmp_path):
    client = create_app(str(tmp_path / "j.db")).test_client()
    r = post(client, "/setup", password="short", confirm="short")
    assert b"at least 12" in r.data


def test_entry_is_encrypted_on_disk(setup):
    post(setup.client, "/entries", text="I felt anxious about work today")
    assert b"I felt anxious about work today" in setup.client.get("/").data
    raw = sqlite3.connect(setup.db).execute("SELECT body FROM entries").fetchone()[0]
    assert b"anxious" not in raw
    assert b"anxious" not in setup.db.read_bytes()


def test_logout_then_wrong_and_right_password(setup):
    post(setup.client, "/entries", text="secret thought")
    post(setup.client, "/logout")
    assert setup.client.get("/").status_code == 302
    r = post(setup.client, "/login", password="wrong password!!")
    assert b"password didn" in r.data
    r = post(setup.client, "/login", password=PASSWORD)
    assert b"secret thought" in r.data


def test_entries_survive_restart(setup):
    post(setup.client, "/entries", text="persisted entry")
    client2 = create_app(str(setup.db)).test_client()
    r = post(client2, "/login", password=PASSWORD)
    assert b"persisted entry" in r.data


def test_post_without_csrf_rejected(setup):
    r = setup.client.post("/entries", data={"text": "x"})
    assert r.status_code == 400


def test_summary_covers_only_entries_since_last_session(setup):
    post(setup.client, "/entries", text="old entry")
    post(setup.client, "/sessions")
    # Make the new entry sort after the session mark even within the same second.
    import vault as v
    orig = v.utc_now
    v.utc_now = lambda: "2999-01-01T00:00:00+00:00"
    try:
        post(setup.client, "/entries", text="new entry")
    finally:
        v.utc_now = orig
    r = post(setup.client, "/summaries")
    assert [e["text"] for e in setup.calls[0]] == ["new entry"]
    assert b"A fake summary" in r.data

    sid = re.search(rb"/summaries/(\d+)/export", r.data).group(1).decode()
    post(setup.client, f"/summaries/{sid}", text="## Overview\nEdited by me.")
    exp = setup.client.get(f"/summaries/{sid}/export")
    assert exp.headers["Content-Disposition"].startswith("attachment")
    assert b"Edited by me." in exp.data
    assert b"Edited by me." not in setup.db.read_bytes()


def test_lockout_after_repeated_failures(setup):
    post(setup.client, "/logout")
    for _ in range(5):
        post(setup.client, "/login", password="nope nope nope")
    r = post(setup.client, "/login", password=PASSWORD)
    assert b"Too many wrong attempts" in r.data


class FakeClient:
    def __init__(self, response):
        self.response = response
        self.kwargs = None
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.kwargs = kwargs
        return self.response


def test_summarize_request_shape():
    resp = SimpleNamespace(stop_reason="end_turn",
                           content=[SimpleNamespace(type="text", text="  summary  ")])
    fake = FakeClient(resp)
    out = summarizer.summarize(
        [{"created_at": "2026-11-02T10:00:00+00:00", "text": "b"},
         {"created_at": "2026-11-01T10:00:00+00:00", "text": "a"}], client=fake)
    assert out == "summary"
    assert fake.kwargs["model"] == "claude-opus-5-5"
    assert fake.kwargs["fallbacks"] == "default"
    body = fake.kwargs["messages"][0]["content"]
    assert body.index("\na\n") < body.index("\nb\n")  # oldest first


def test_summarize_refusal_is_reported():
    fake = FakeClient(SimpleNamespace(stop_reason="refusal", content=[]))
    with pytest.raises(summarizer.SummaryError):
        summarizer.summarize([{"created_at": "2026-11-01T00:00:00+00:00", "text": "x"}], client=fake)


def test_summarize_needs_entries():
    with pytest.raises(summarizer.SummaryError):
        summarizer.summarize([], client=FakeClient(None))


def test_transcribe_returns_text_and_saves_nothing(setup):
    setup.client.get("/")
    r = setup.client.post("/transcribe", data={
        "csrf": csrf_of(setup.client),
        "audio": (io.BytesIO(b"fake-audio-bytes"), "recording"),
    }, content_type="multipart/form-data")
    assert r.get_json() == {"text": "I talked about my week"}
    assert setup.calls == [b"fake-audio-bytes"]
    assert b"fake-audio-bytes" not in setup.db.read_bytes()
    assert sqlite3.connect(setup.db).execute("SELECT COUNT(*) FROM entries").fetchone()[0] == 0


def test_transcribe_requires_login(tmp_path):
    client = create_app(str(tmp_path / "j.db"), transcribe_fn=lambda a: "x").test_client()
    client.get("/setup")
    r = client.post("/transcribe", data={"csrf": csrf_of(client)})
    assert r.status_code == 302


def test_transcribe_empty_audio_errors():
    with pytest.raises(transcriber.TranscriptionError):
        transcriber.transcribe(b"")
