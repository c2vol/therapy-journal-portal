# Between sessions: a private therapy journal

A small web app for talking or typing journal entries between therapy
sessions. When a session is coming up, one button asks Claude to organize the
entries since your last session into a short summary you can read over, edit,
and hand to your therapist. It organizes what you wrote; it doesn't interpret
or diagnose.

This first version is just for you and runs on your own computer. A shared
portal for your therapist can come later, once you know what she wants.

## Running it

You need Python 3.10 or newer and an Anthropic API key
(from https://console.anthropic.com).

```bash
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...  # Windows: set ANTHROPIC_API_KEY=...
python app.py
```

Then open http://127.0.0.1:5000. The first visit asks you to choose a password.
There's no email or account: the app only runs on your computer, for you.

The first time you record, give it a few minutes to download the speech model.
For better accuracy at the cost of speed, start the app with
`WHISPER_MODEL=small.en`.

To run the tests: `pip install pytest && pytest`.

## How to use it

1. Whenever something's on your mind, press **Tap to talk** and say it, as long
   as you like. Tap again when you're done. What you said shows up as text so
   you can fix any mistakes, then press **Save entry**. You can also just type.
2. Before a session, press **Summarize for my therapist**. Read it over and fix
   anything that doesn't sound like you, then **Download** it or
   **Print / save as PDF**.
3. After the session, press **I had a session today**. The next summary will
   only cover entries written after that.

## Privacy: what's protected and what isn't

- **Login.** Everything is behind your password. The app locks itself after
  20 minutes of inactivity, when you press **Lock**, or when it restarts. After
  5 wrong passwords it waits a minute before letting you try again.
- **Encrypted storage.** Entries and summaries are encrypted before they touch
  the disk, with a key that is itself locked by your password (details at the
  top of `vault.py`). The database file at `data/journal.db` is unreadable
  without your password. Entry *timestamps* are not encrypted, so someone with
  the file could see when you wrote, but not what.
- **No password reset.** Because the encryption is real, a forgotten password
  means the entries are gone. Keep it somewhere safe.
- **Only on your computer.** The app listens on 127.0.0.1, so other devices on
  your network can't reach it. Your data never goes into this Git repo
  (`data/` is in `.gitignore`).
- **What leaves your computer:**
  - When you press **Summarize**, the entries in that period are sent to
    Anthropic's API. Anthropic doesn't train models on API data by default;
    see their privacy policy for how long it's kept.
  - **Voice** never leaves your computer. Recordings are transcribed locally
    with the open Whisper model and then thrown away; only the text is kept
    (encrypted). The one network step is a one-time download of the speech
    model (about 150 MB) from Hugging Face the first time you record.
- **Back up** `data/journal.db` if you don't want to risk losing entries. The
  backup stays encrypted.

## How it's built (and why)

| Piece | Choice | Why |
|---|---|---|
| Language | Python | Readable, and good for learning. |
| Web framework | [Flask](https://flask.palletsprojects.com) | Tiny and explicit: each page is one function in `app.py`. |
| Database | SQLite | A single file, no database server to install. |
| Encryption | [`cryptography`](https://cryptography.io) Fernet + scrypt | Well-reviewed building blocks, so we don't invent our own crypto. |
| Summaries | Anthropic Python SDK, model `claude-opus-5-5` | The one part that needs AI. The prompt is in `summarize.py`. |
| Voice | Browser recording + [faster-whisper](https://github.com/SYSTRAN/faster-whisper) | Transcribes on your own computer, so your voice stays private. |

Files:

- `app.py`: pages, login, and security settings.
- `vault.py`: encrypted storage.
- `summarize.py`: the Claude prompt and API call.
- `transcribe.py`: turns recordings into text, locally.
- `templates/` and `static/`: the HTML, CSS and a little JavaScript.
- `tests/`: automated checks, including one that confirms entries aren't
  readable in the database file.
