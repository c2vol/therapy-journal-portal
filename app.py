"""The web app: pages, login, and the glue between the vault and the summarizer.

Run it with `python app.py` and open http://127.0.0.1:5000 in your browser.
It only listens on your own computer (127.0.0.1), so nobody else on your
network can reach it.

Run `python app.py --phone` instead to also use it from your phone on the same
Wi-Fi. That mode uses https (see certs.py) so the microphone works on the phone
and your entries aren't readable on the network.
"""

import hmac
import os
import secrets
import time
from datetime import datetime, timezone
from functools import wraps

from flask import (Flask, Response, abort, flash, g, jsonify, redirect,
                   render_template, request, session, url_for)

import summarize as summarizer
import transcribe as transcriber
from vault import Vault, WrongPassword

IDLE_TIMEOUT_SECONDS = 20 * 60   # lock after 20 minutes without activity
MIN_PASSWORD_LENGTH = 12
MAX_FAILED_LOGINS = 5
LOCKOUT_SECONDS = 60


def create_app(db_path: str | None = None, summarize_fn=None,
               transcribe_fn=None, api_enabled: bool | None = None) -> Flask:
    app = Flask(__name__)
    # A fresh random secret on every start. Restarting the app signs you out,
    # which is fine because the unlocked key lives only in memory anyway.
    app.secret_key = secrets.token_bytes(32)
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        MAX_CONTENT_LENGTH=50 * 1024 * 1024,  # recordings up to ~50 MB
    )

    vault = Vault(db_path or os.environ.get("JOURNAL_DB", "data/journal.db"))
    summarize_fn = summarize_fn or summarizer.summarize
    transcribe_fn = transcribe_fn or transcriber.transcribe
    # With an Anthropic API key, the app can summarize by itself (costs a few cents).
    # Without one, everything still works: you copy the entries into your own
    # Claude app and paste the summary back, which costs nothing extra.
    if api_enabled is None:
        api_enabled = bool(os.environ.get("ANTHROPIC_API_KEY"))

    # Unlocked keys, kept only in this process's memory: {session_id: [key, last_seen]}
    unlocked: dict[str, list] = {}
    failed = {"count": 0, "until": 0.0}

    # ---- helpers ---------------------------------------------------------

    def current_key() -> bytes | None:
        sid = session.get("sid")
        item = unlocked.get(sid) if sid else None
        if item is None:
            return None
        if time.time() - item[1] > IDLE_TIMEOUT_SECONDS:
            unlocked.pop(sid, None)
            return None
        item[1] = time.time()
        return item[0]

    def start_session(key: bytes) -> None:
        session.clear()
        sid = secrets.token_urlsafe(32)
        unlocked[sid] = [key, time.time()]
        session["sid"] = sid

    def login_required(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            key = current_key()
            if key is None:
                if not vault.is_set_up():
                    return redirect(url_for("setup"))
                return redirect(url_for("login"))
            g.key = key
            return view(*args, **kwargs)
        return wrapper

    @app.before_request
    def check_csrf():
        # Every form carries a secret token so other websites can't submit
        # forms to this app on your behalf.
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(32)
        if request.method == "POST":
            sent = request.form.get("csrf", "")
            if not hmac.compare_digest(sent, session["csrf"]):
                abort(400, "Form expired. Go back, reload the page and try again.")

    @app.after_request
    def security_headers(resp):
        resp.headers["Cache-Control"] = "no-store"   # don't keep journal pages in browser cache
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:"
        )
        return resp

    @app.context_processor
    def inject():
        return {"csrf": session.get("csrf", "")}

    @app.template_filter("pretty_date")
    def pretty_date(value):
        if not value:
            return ""
        dt = datetime.fromisoformat(value).astimezone()  # show in local time
        return dt.strftime("%a %b %-d, %Y · %-I:%M %p")

    # ---- setup and login ---------------------------------------------------

    @app.route("/setup", methods=["GET", "POST"])
    def setup():
        if vault.is_set_up():
            return redirect(url_for("login"))
        if request.method == "POST":
            pw = request.form.get("password", "")
            if len(pw) < MIN_PASSWORD_LENGTH:
                flash(f"Use at least {MIN_PASSWORD_LENGTH} characters. A short sentence works well.")
            elif pw != request.form.get("confirm", ""):
                flash("The two passwords don't match.")
            else:
                start_session(vault.set_up(pw))
                return redirect(url_for("journal"))
        return render_template("setup.html", min_len=MIN_PASSWORD_LENGTH)

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if not vault.is_set_up():
            return redirect(url_for("setup"))
        if request.method == "POST":
            if time.time() < failed["until"]:
                flash("Too many wrong attempts. Wait a minute and try again.")
            else:
                try:
                    key = vault.unlock(request.form.get("password", ""))
                except WrongPassword:
                    failed["count"] += 1
                    if failed["count"] >= MAX_FAILED_LOGINS:
                        failed["count"] = 0
                        failed["until"] = time.time() + LOCKOUT_SECONDS
                    flash("That password didn't work.")
                else:
                    failed["count"] = 0
                    start_session(key)
                    return redirect(url_for("journal"))
        return render_template("login.html")

    @app.post("/logout")
    def logout():
        unlocked.pop(session.get("sid"), None)
        session.clear()
        return redirect(url_for("login"))

    # ---- journal -----------------------------------------------------------

    @app.get("/")
    @login_required
    def journal():
        last = vault.last_session()
        entries = vault.list_entries(g.key)
        since_count = sum(1 for e in entries if not last or e["created_at"] > last)
        return render_template(
            "journal.html",
            entries=entries,
            last_session=last,
            since_count=since_count,
            summaries=vault.list_summaries(),
            api_enabled=api_enabled,
        )

    @app.post("/entries")
    @login_required
    def add_entry():
        text = request.form.get("text", "").strip()
        if text:
            vault.add_entry(g.key, text)
            flash("Entry saved.")
        return redirect(url_for("journal"))

    @app.post("/entries/<int:entry_id>/delete")
    @login_required
    def delete_entry(entry_id):
        vault.delete_entry(entry_id)
        flash("Entry deleted.")
        return redirect(url_for("journal"))

    @app.post("/sessions")
    @login_required
    def mark_session():
        vault.mark_session()
        flash("Marked a therapy session today. New entries start a fresh period.")
        return redirect(url_for("journal"))

    # ---- voice ---------------------------------------------------------------

    @app.post("/transcribe")
    @login_required
    def transcribe_audio():
        # Your recording arrives here, is turned into text on this computer,
        # and is then thrown away. Only the text goes back to the page.
        audio = request.files.get("audio")
        try:
            return jsonify(text=transcribe_fn(audio.read() if audio else b""))
        except transcriber.TranscriptionError as e:
            return jsonify(error=str(e)), 400

    # ---- summaries -----------------------------------------------------------

    @app.get("/summaries/new")
    @login_required
    def new_summary():
        since = vault.last_session()
        entries = vault.list_entries(g.key, since=since)
        if not entries:
            flash("There are no entries in this period to summarize.")
            return redirect(url_for("journal"))
        return render_template("summary_new.html", prompt=summarizer.build_prompt(entries),
                               count=len(entries), since=since)

    @app.post("/summaries/manual")
    @login_required
    def save_manual_summary():
        text = request.form.get("text", "").strip()
        if not text:
            flash("Paste the summary from Claude into the box first.")
            return redirect(url_for("new_summary"))
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        summary_id = vault.save_summary(g.key, text, vault.last_session(), now)
        return redirect(url_for("show_summary", summary_id=summary_id))

    @app.post("/summaries")
    @login_required
    def create_summary():
        if not api_enabled:
            return redirect(url_for("new_summary"))
        since = vault.last_session()
        entries = vault.list_entries(g.key, since=since)
        try:
            text = summarize_fn(entries)
        except summarizer.SummaryError as e:
            flash(str(e))
            return redirect(url_for("journal"))
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        summary_id = vault.save_summary(g.key, text, since, now)
        return redirect(url_for("show_summary", summary_id=summary_id))

    @app.route("/summaries/<int:summary_id>", methods=["GET", "POST"])
    @login_required
    def show_summary(summary_id):
        if request.method == "POST":
            vault.update_summary(g.key, summary_id, request.form.get("text", ""))
            flash("Summary saved.")
            return redirect(url_for("show_summary", summary_id=summary_id))
        s = vault.get_summary(g.key, summary_id)
        if s is None:
            abort(404)
        return render_template("summary.html", s=s)

    @app.get("/summaries/<int:summary_id>/export")
    @login_required
    def export_summary(summary_id):
        s = vault.get_summary(g.key, summary_id)
        if s is None:
            abort(404)
        start = pretty_date(s["period_start"]) if s["period_start"] else "the beginning"
        header = (
            "# Journal summary\n\n"
            f"_Entries from {start} to {pretty_date(s['period_end'])}. "
            "Organized from my own journal entries with an AI tool; not a clinical assessment._\n\n"
        )
        day = datetime.fromisoformat(s["period_end"]).astimezone().strftime("%Y-%m-%d")
        return Response(
            header + s["text"] + "\n",
            mimetype="text/markdown",
            headers={"Content-Disposition": f'attachment; filename="journal-summary-{day}.md"'},
        )

    @app.get("/summaries/<int:summary_id>/print")
    @login_required
    def print_summary(summary_id):
        s = vault.get_summary(g.key, summary_id)
        if s is None:
            abort(404)
        return render_template("print.html", s=s)

    return app


if __name__ == "__main__":
    import sys

    port = int(os.environ.get("PORT", 5000))
    app = create_app()
    if "--phone" in sys.argv:
        # Phone mode: reachable from other devices on your home Wi-Fi, over https.
        import certs

        cert = certs.ensure_cert(os.path.dirname(os.path.abspath(
            os.environ.get("JOURNAL_DB", "data/journal.db"))))
        app.config["SESSION_COOKIE_SECURE"] = True
        print(f"\nOn your phone (same Wi-Fi), open:  https://{certs.local_name()}:{port}")
        print(f"   or, if that doesn't load:     https://{certs.lan_ip()}:{port}")
        print("Your phone will warn that the certificate isn't trusted. That's expected")
        print("for a home setup: choose 'Show details' / 'Advanced' and continue.\n")
        app.run(host="0.0.0.0", port=port, ssl_context=cert)
    else:
        app.run(host="127.0.0.1", port=port)
