"""Turns journal entries into a short summary for your therapist, using Claude.

This is the only part of the app that sends anything off your computer: the
entries you choose to summarize go to Anthropic's API, and the summary comes
back. Nothing is sent until you press the button.
"""

import anthropic

MODEL = "claude-opus-5-5"

SYSTEM_PROMPT = """\
You help a person get ready for their next therapy session. You will receive \
the journal entries they wrote since their last session, each with a date and \
time. Turn them into a short, clean summary they can hand to their therapist.

Your job is to organize, not to interpret:
- Do not diagnose, name conditions, give advice, or guess at causes or hidden \
meanings. Leave all of that to the therapist.
- Write in the first person, as the person ("I felt...", "I noticed..."), and \
keep their own words and framing where you can.
- Only include what is in the entries. Keep dates and events accurate.
- If an entry mentions self-harm, suicidal thoughts, or not feeling safe, \
include it plainly and near the top, in their words. Never soften or leave it out.

Format the summary in Markdown, leaving out any section that would be empty:
## Overview
Two or three sentences on how the period went overall.
## Main themes
Short bullets for topics that came up more than once.
## Notable moments
Short dated bullets for specific events or feelings worth mentioning.
## Things I want to bring up
Anything the entries say the person wants to talk about in session.

Keep it under about 400 words. Output only the summary, with no preamble.\
"""


class SummaryError(Exception):
    """A problem worth showing to the user in plain words."""


def format_entries(entries: list[dict]) -> str:
    # Oldest first reads more naturally as a timeline.
    parts = []
    for e in sorted(entries, key=lambda e: e["created_at"]):
        parts.append(f'<entry written="{e["created_at"]} UTC">\n{e["text"]}\n</entry>')
    return "\n\n".join(parts)


def summarize(entries: list[dict], client: anthropic.Anthropic | None = None) -> str:
    if not entries:
        raise SummaryError("There are no entries in this period to summarize.")
    client = client or anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment

    try:
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            # A summary is a light task, so medium effort keeps it quick and cheap.
            output_config={"effort": "medium"},
            # If the model declines (its safety filters can misfire on sensitive
            # topics like mental health), Anthropic retries on a fallback model.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=[
                {
                    "role": "user",
                    "content": "Here are my journal entries since my last session:\n\n"
                    + format_entries(entries),
                }
            ],
        )
    except anthropic.AuthenticationError:
        raise SummaryError("The Anthropic API key is missing or wrong. Check ANTHROPIC_API_KEY.")
    except anthropic.RateLimitError:
        raise SummaryError("The Anthropic API is busy or your usage limit was hit. Try again in a minute.")
    except anthropic.APIStatusError as e:
        raise SummaryError(f"The Anthropic API returned an error ({e.status_code}). Try again shortly.")
    except anthropic.APIConnectionError:
        raise SummaryError("Couldn't reach the Anthropic API. Check your internet connection.")

    if response.stop_reason == "refusal":
        raise SummaryError("The model declined to summarize these entries. You can try again, or write the summary by hand.")

    text = "".join(b.text for b in response.content if b.type == "text").strip()
    if not text:
        raise SummaryError("The model returned an empty summary. Try again.")
    return text
