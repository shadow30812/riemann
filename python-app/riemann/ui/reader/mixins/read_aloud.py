"""
Read aloud: speaks the document from the current page with the system's
text-to-speech engine (Qt TextToSpeech), one sentence at a time, highlighting
the sentence being spoken and turning pages as it goes.

Sentences come from the same character geometry text selection uses, so the
highlight lines up with the text on the page. Speech rate and voice are user
settings (READ_RATE_KEY, READ_VOICE_KEY). On Linux the engine is
speech-dispatcher; when Qt cannot load it, the reader says what to install
instead of failing silently.

Ctrl+Shift+U or the toolbar button starts and stops it; Esc stops it too.
"""

import re
from typing import List, Optional, Tuple

from PySide6.QtCore import QTimer

from ....core.settings import app_settings
from .host import ReaderHostBase

READ_RATE_KEY = "reader/read_aloud_rate"
READ_VOICE_KEY = "reader/read_aloud_voice"
READ_ENGINE_KEY = "reader/read_aloud_engine"
SENTENCE = re.compile(r"\S.*?(?:[.!?]+[\"'”’)\]]*(?=\s|$)|$)", re.S)

NO_ENGINE = (
    "Read aloud needs a speech engine. On Linux, install speech-dispatcher "
    "and a voice such as espeak-ng with your package manager, then restart Riemann."
)


def split_sentences(text: str) -> List[Tuple[int, int]]:
    """(start, end) character offsets of each sentence in text."""
    return [(m.start(), m.end()) for m in SENTENCE.finditer(text) if m.group().strip()]


def make_speech(engine: str = ""):
    """A QTextToSpeech for engine ("" for the platform default), or None when none works."""
    from PySide6.QtTextToSpeech import QTextToSpeech

    tts = QTextToSpeech(engine) if engine else QTextToSpeech()
    if tts.state() == QTextToSpeech.State.Error:
        tts.deleteLater()
        return None
    return tts


class ReadAloudMixin(ReaderHostBase):
    _tts = None
    _reading = False

    def _speech(self):
        tts = self._tts
        if tts is None:
            engine = str(app_settings().value(READ_ENGINE_KEY, "") or "")
            tts = make_speech(engine)
            if tts is None:
                return None
            tts.stateChanged.connect(self._on_speech_state)
            self._tts = tts
        rate = app_settings().value(READ_RATE_KEY, 0.0, type=float)
        tts.setRate(max(-1.0, min(1.0, rate)))
        voice_name = str(app_settings().value(READ_VOICE_KEY, "") or "")
        if voice_name:
            for voice in tts.availableVoices():
                if voice.name() == voice_name:
                    tts.setVoice(voice)
                    break
        return tts

    def is_reading_aloud(self) -> bool:
        return bool(self._reading)

    def toggle_read_aloud(self) -> None:
        if self.is_reading_aloud():
            self.stop_read_aloud()
        else:
            self.start_read_aloud()

    def start_read_aloud(self, page: Optional[int] = None) -> None:
        if not self.current_doc:
            return
        tts = self._speech()
        if tts is None:
            self.show_toast(NO_ENGINE)
            self._set_read_button(False)
            return
        self._reading = True
        self._read_page = self.current_page_index if page is None else page
        self._read_queue = self._sentences(self._read_page)
        self._read_waiting = False
        self._set_read_button(True)
        self._speak_next()

    def stop_read_aloud(self) -> None:
        self._reading = False
        self._read_queue = []
        tts = self._tts
        if tts is not None:
            tts.stop()
        self._show_spoken(None, [])
        self._set_read_button(False)

    def _set_read_button(self, on: bool) -> None:
        button = self.btn_read_aloud
        if button is not None:
            button.setChecked(on)

    def _sentences(self, page: int) -> List[Tuple[str, int, int]]:
        if page not in self.page_widgets:
            self.jump_to_page(page)
        chars = self._get_page_chars(page)
        text = "".join(c[0] for c in chars)
        out = []
        for start, end in split_sentences(text):
            spoken = " ".join(text[start:end].split())
            if re.search(r"\w", spoken):
                out.append((spoken, start, end))
        return out

    def _speak_next(self) -> None:
        if not self.is_reading_aloud():
            return
        while not self._read_queue:
            if self._read_page + 1 >= self.current_doc.page_count:
                self.stop_read_aloud()
                return
            self._read_page += 1
            self.jump_to_page(self._read_page)
            self._read_queue = self._sentences(self._read_page)
        text, start, end = self._read_queue.pop(0)
        chars = self._get_page_chars(self._read_page)
        rects = []
        if chars and end > start:
            rects, _ = self._rects_for_char_range(chars, start, min(end, len(chars)) - 1)
        self._show_spoken(self._read_page, rects)
        self._read_waiting = True
        self._tts.say(text)

    def _show_spoken(self, page: Optional[int], rects: list) -> None:
        for idx, widget in self.page_widgets.items():
            if hasattr(widget, "set_spoken_rects"):
                widget.set_spoken_rects(rects if idx == page else [])
        if page is not None and rects:
            top = self._page_top_y(page)
            widget = self.page_widgets.get(page)
            if top is not None and widget is not None:
                row = widget.parentWidget()
                x = (row.x() if row else 0) + widget.x() + rects[0].center().x()
                self.scroll.ensureVisible(int(x), int(top + rects[0].center().y()), 40, 120)

    def _on_speech_state(self, state) -> None:
        from PySide6.QtTextToSpeech import QTextToSpeech

        if state == QTextToSpeech.State.Error:
            self.stop_read_aloud()
            self.show_toast("The speech engine stopped with an error.")
        elif state == QTextToSpeech.State.Ready and self._read_waiting and self.is_reading_aloud():
            self._read_waiting = False
            QTimer.singleShot(0, self, self._speak_next)
