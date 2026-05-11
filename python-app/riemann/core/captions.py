import numpy as np

_whisper_model = None


def get_whisper_model():
    global _whisper_model
    if _whisper_model is None:
        try:
            from faster_whisper import WhisperModel

            _whisper_model = WhisperModel(
                "base", device="cpu", compute_type="int8", download_root="./models"
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
    segments, info = model.transcribe(audio_chunk, beam_size=5, task="translate")
    translated_text = " ".join([segment.text for segment in segments])
    return translated_text
