import io
import logging
import os

import openai

logger = logging.getLogger(__name__)

_client: openai.OpenAI | None = None


def _get_client() -> openai.OpenAI:
    global _client
    if _client is None:
        _client = openai.OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return _client


def transcribe_audio(audio_bytes: bytes, mime_type: str = "audio/ogg") -> str:
    """Transcribe audio bytes using OpenAI Whisper."""
    ext_map = {
        "audio/ogg": "ogg",
        "audio/ogg; codecs=opus": "ogg",
        "audio/mpeg": "mp3",
        "audio/mp4": "m4a",
        "audio/wav": "wav",
    }
    ext = ext_map.get(mime_type, "ogg")
    file = io.BytesIO(audio_bytes)
    file.name = f"audio.{ext}"

    client = _get_client()
    transcript = client.audio.transcriptions.create(
        model="whisper-1",
        file=file,
        language="es",
    )
    return transcript.text
