# microphone capture + speech-to-text for the voice input toggle
#
# Records from the default mic while active, then sends the WAV audio to
# Gemini for transcription (reuses the same GOOGLE_API_KEY as the agent LLM).

import io
import os
import wave

import numpy as np
import sounddevice as sd
from google import genai
from google.genai import types

SAMPLE_RATE = 16000
CHANNELS = 1


class VoiceRecorder:

    def __init__(self, sample_rate: int = SAMPLE_RATE):
        self.sample_rate = sample_rate
        self._frames: list[np.ndarray] = []
        self._stream: sd.InputStream | None = None

    def start(self):
        self._frames = []
        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=CHANNELS,
            dtype='int16',
            callback=self._on_audio,
        )
        self._stream.start()

    def _on_audio(self, indata, _frames, _time_info, _status):
        self._frames.append(indata.copy())

    def stop(self) -> bytes:
        if self._stream is None:
            return b''
        self._stream.stop()
        self._stream.close()
        self._stream = None

        if not self._frames:
            return b''
        audio = np.concatenate(self._frames, axis=0)

        buf = io.BytesIO()
        with wave.open(buf, 'wb') as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(2)  # int16
            wf.setframerate(self.sample_rate)
            wf.writeframes(audio.tobytes())
        return buf.getvalue()


def transcribe(audio_bytes: bytes) -> str:
    api_key = os.environ.get('GOOGLE_API_KEY')
    if not api_key:
        raise RuntimeError('GOOGLE_API_KEY is not set (check your .env file).')

    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model='gemini-flash-latest',
        contents=[
            types.Part.from_bytes(data=audio_bytes, mime_type='audio/wav'),
            'Transcribe this audio exactly as spoken. '
            'Respond with only the transcription, no extra commentary.',
        ],
    )
    return (response.text or '').strip()
