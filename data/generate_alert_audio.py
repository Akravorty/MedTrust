# data/generate_alert_audio.py
"""
Generates spoken-alert MP3s for the languages/statuses actually needed for
the demo: English, Hindi, Odia, Marathi x HOLD/REJECT/RECALL.

Supersedes the old ad-hoc data/multilingual_alerts.py, which had its own
separate 2-language TRANSLATIONS dict duplicating a subset of what
shared/alert_texts.py now covers for 19 languages. This script pulls text
from that single source of truth instead, so audio wording can never
drift out of sync with what the app actually displays/returns as text.

There is no ACCEPT entry here on purpose: an ACCEPTed batch has nothing to
alert about, so services/alerts/service.py never calls send_alert for
ACCEPT in the first place -- generating audio for a status that's never
actually sent would just be dead weight.

Usage:
    python data/generate_alert_audio.py
Requires ELEVENLABS_API_KEY in .env (free tier is enough -- this is ~12
short clips, well under the free monthly character quota).

Idempotent: skips any file that already exists, so re-running after adding
a language later only generates what's missing.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from shared.alert_texts import ALERT_TEXTS

AUDIO_DIR = Path(__file__).resolve().parent / "audio"

# Languages actually needed for the demo. Extend this tuple later if more
# languages get reviewed -- the loop below picks up any code present in
# both this list and ALERT_TEXTS automatically.
TARGET_LANGUAGES = ("en", "hi", "or", "mr")
TARGET_STATUSES = ("HOLD", "REJECT", "RECALL")

# One neutral multilingual voice for all languages, matching the working
# pattern from the original script. Swap this if your ElevenLabs account
# has a preferred voice already selected.
VOICE_ID = "EXAVITQu4vr4xnSDxMaL"
MODEL_ID = "eleven_multilingual_v2"


def generate_all(force: bool = False) -> None:
    api_key = os.getenv("ELEVENLABS_API_KEY")
    if not api_key:
        print("ELEVENLABS_API_KEY is not set in .env -- nothing to generate.")
        print("Get a free-tier key at https://elevenlabs.io, add it to .env, and re-run.")
        return

    from elevenlabs.client import ElevenLabs

    client = ElevenLabs(api_key=api_key)
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)

    generated, skipped, failed = 0, 0, 0

    for lang in TARGET_LANGUAGES:
        texts = ALERT_TEXTS.get(lang)
        if not texts:
            print(f"  [skip] no text for language '{lang}' in shared/alert_texts.py")
            continue

        for status in TARGET_STATUSES:
            text = texts.get(status)
            if not text:
                print(f"  [skip] no '{status}' text for '{lang}'")
                continue

            filename = f"alert_{lang}_{status.lower()}.mp3"
            file_path = AUDIO_DIR / filename

            if file_path.exists() and not force:
                print(f"  [exists] {filename}")
                skipped += 1
                continue

            try:
                print(f"  [generating] {filename} ...")
                audio = client.text_to_speech.convert(
                    text=text, voice_id=VOICE_ID, model_id=MODEL_ID,
                    output_format="mp3_44100_128",
                )
                with open(file_path, "wb") as f:
                    for chunk in audio:
                        if chunk:
                            f.write(chunk)
                generated += 1
            except Exception as exc:  # noqa: BLE001 — report and keep going
                print(f"  [FAILED] {filename}: {exc}")
                failed += 1

    print(f"\nDone. Generated {generated}, already had {skipped}, failed {failed}.")
    if failed:
        print("Failed clips: check your ElevenLabs quota/key and re-run (safe to re-run, it skips existing files).")


if __name__ == "__main__":
    generate_all()
