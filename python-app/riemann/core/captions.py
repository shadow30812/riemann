from collections import deque

import numpy as np

_whisper_model = None
_text_memory = deque(maxlen=15)


def get_whisper_model():
    global _whisper_model
    if _whisper_model is None:
        try:
            from faster_whisper import WhisperModel

            _whisper_model = WhisperModel(
                "base",
                device="cpu",
                compute_type="int8",
                download_root="./models",
                cpu_threads=8,
            )

        except ImportError:
            raise RuntimeError(
                "The 'faster-whisper' module is not installed. "
                "Live captioning requires this module to be available in the environment."
            )
    return _whisper_model


def process_audio_chunk(audio_chunk: np.ndarray):
    """
    Called by your WebSocket endpoint when audio chunks arrive from the browser.
    """
    model = get_whisper_model()
    context_prompt = " ".join(_text_memory)
    segments, info = model.transcribe(
        audio_chunk,
        beam_size=1,
        task="translate",
        vad_filter=True,
        condition_on_previous_text=False,
        initial_prompt=context_prompt if context_prompt else None,
    )
    translated_text = " ".join([segment.text for segment in segments]).strip()
    if translated_text:
        words = translated_text.split()
        for word in words:
            _text_memory.append(word)
    return translated_text
