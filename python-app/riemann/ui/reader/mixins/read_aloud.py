"""
Read aloud: speaks the document from the current page with the system's
text-to-speech engine (Qt TextToSpeech), one sentence at a time, highlighting
the sentence being spoken and turning pages as it goes.

ReadAloud is given two functions: page_chars(page), the character boxes of a
page, and page_count(). It reports through signals: page_needed(page) before
reading a page's sentences, so the reader can lay it out; page_turned(page)
when it moves on to the next page; sentence_shown(page, rects) and
word_shown(page, rects) for the highlights (page None and no rects to clear
them); reading_changed(on) for the toolbar button; and toast for messages.

Sentences come from the same character geometry text selection uses, so the
highlight lines up with the text on the page. Speech rate and voice are user
settings (READ_RATE_KEY, READ_VOICE_KEY). On Linux the engine is
speech-dispatcher; when Qt cannot load it, the reader says what to install
instead of failing silently. Qt's plugin links against libspeechd, which
distributions package apart from the daemon, so the daemon alone is not
enough.

Ctrl+Shift+U or the toolbar button starts and stops it; Esc stops it too.

The word being spoken is highlighted over its sentence when the setting
READ_WORDS_KEY is on (Settings, Accessibility). Each queued sentence keeps
offsets, the page character behind each character of the spoken text (whose
whitespace is collapsed), so a word's place in the spoken text maps back to
boxes on the page. Engines with WordByWordProgress (Windows, macOS, Android,
Flite) say which word they are on through sayingWord. Others, such as some
speech-dispatcher setups, don't; then a timer moves the highlight at the
speaking speed, chars_per_second, which starts from WORDS_CPS scaled by the
rate setting and is learnt from how long each finished sentence took, so the
highlight drifts at most within a sentence.
"""

import re
from collections.abc import Callable

from PySide6.QtCore import QElapsedTimer, QObject, QTimer, Signal

from ....core.settings import READ_WORDS_KEY, app_settings
from .selection import rects_for_char_range

READ_RATE_KEY = "reader/read_aloud_rate"
READ_VOICE_KEY = "reader/read_aloud_voice"
READ_ENGINE_KEY = "reader/read_aloud_engine"
WORDS_CPS = 15.0
WORD_TICK_MS = 50
WORD = re.compile(r"\S+")
SENTENCE = re.compile(r"\S.*?(?:[.!?]+[\"'”’)\]]*(?=\s|$)|$)", re.S)

NO_ENGINE = (
    "Read aloud needs a speech engine. On Linux, install speech-dispatcher, "
    "its client library (speech-dispatcher-libs on Fedora and RHEL, libspeechd2 "
    "on Debian and Ubuntu) and a voice such as espeak-ng with your package "
    "manager, then restart Riemann."
)

def split_sentences(text: str) -> list[tuple[int, int]]:
    """(start, end) character offsets of each sentence in text."""
    return [(m.start(), m.end()) for m in SENTENCE.finditer(text) if m.group().strip()]


def collapse_spaces(text: str, start: int, end: int) -> tuple[str, list[int]]:
    """text[start:end] with whitespace runs made one space, and the index in text of each kept character."""
    out: list[str] = []
    offsets: list[int] = []
    gap = False
    for i in range(start, end):
        ch = text[i]
        if ch.isspace():
            gap = bool(out)
            continue
        if gap:
            out.append(" ")
            offsets.append(i - 1)
            gap = False
        out.append(ch)
        offsets.append(i)
    return "".join(out), offsets


def word_at(text: str, position: int) -> tuple[int, int] | None:
    """(start, length) of the word in text at or after position, or None past the end."""
    for match in WORD.finditer(text):
        if match.end() > position:
            return match.start(), match.end() - match.start()
    return None


def make_speech(engine: str = ""):
    """A QTextToSpeech for engine ("" for the platform default), or None when none works."""
    from PySide6.QtTextToSpeech import QTextToSpeech

    tts = QTextToSpeech(engine) if engine else QTextToSpeech()
    if tts.state() == QTextToSpeech.State.Error:
        tts.deleteLater()
        return None
    return tts


def sentences(chars: list[tuple]) -> list[tuple[str, int, int, list[int]]]:
    """(spoken text, start, end, offsets) for each sentence with a word in it, from a page's characters."""
    text = "".join(c[0] for c in chars)
    out = []
    for start, end in split_sentences(text):
        spoken, offsets = collapse_spaces(text, start, end)
        if re.search(r"\w", spoken):
            out.append((spoken, start, end, offsets))
    return out


class ReadAloud(QObject):
    sentence_shown = Signal(object, list)
    word_shown = Signal(object, list)
    page_turned = Signal(int)
    page_needed = Signal(int)
    reading_changed = Signal(bool)
    toast = Signal(str)

    def __init__(
        self,
        page_chars: Callable[[int], list],
        page_count: Callable[[], int],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.page_chars = page_chars
        self.page_count = page_count
        self.chars_per_second = 0.0
        self._tts = None
        self._reading = False
        self._read_page = 0
        self._read_queue: list = []
        self._read_waiting = False
        self._spoken = None
        self._word_timer: QTimer | None = None
        self._word_clock: QElapsedTimer | None = None
        self._word_shown = None

    def _speech(self):
        tts = self._tts
        if tts is None:
            engine = str(app_settings().value(READ_ENGINE_KEY, "") or "")
            tts = make_speech(engine)
            if tts is None:
                return None
            tts.stateChanged.connect(self._on_speech_state)
            tts.sayingWord.connect(self._on_saying_word)
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

    def is_reading(self) -> bool:
        return bool(self._reading)

    def toggle(self, page: int) -> None:
        if self.is_reading():
            self.stop()
        else:
            self.start(page)

    def start(self, page: int) -> None:
        """Reads aloud from the start of page."""
        if self.page_count() <= 0:
            return
        tts = self._speech()
        if tts is None:
            self.toast.emit(NO_ENGINE)
            self.reading_changed.emit(False)
            return
        self._reading = True
        self._read_page = page
        self.page_needed.emit(page)
        self._read_queue = sentences(self.page_chars(page))
        self._read_waiting = False
        self.reading_changed.emit(True)
        self._speak_next()

    def stop(self) -> None:
        self._reading = False
        self._read_queue = []
        tts = self._tts
        if tts is not None:
            tts.stop()
        self.sentence_shown.emit(None, [])
        self._stop_word_pacing()
        self._show_word(None)
        self._spoken = None
        self.reading_changed.emit(False)

    def _speak_next(self) -> None:
        if not self.is_reading():
            return
        while not self._read_queue:
            if self._read_page + 1 >= self.page_count():
                self.stop()
                return
            self._read_page += 1
            self.page_turned.emit(self._read_page)
            self.page_needed.emit(self._read_page)
            self._read_queue = sentences(self.page_chars(self._read_page))
        text, start, end, offsets = self._read_queue.pop(0)
        chars = self.page_chars(self._read_page)
        rects = []
        if chars and end > start:
            rects, _ = rects_for_char_range(chars, start, min(end, len(chars)) - 1)
        self.sentence_shown.emit(self._read_page, rects)
        self._spoken = (self._read_page, chars, text, offsets)
        self._word_shown = None
        self._read_waiting = True
        self._tts.say(text)

    def _on_speech_state(self, state) -> None:
        from PySide6.QtTextToSpeech import QTextToSpeech

        if state == QTextToSpeech.State.Error:
            self.stop()
            self.toast.emit("The speech engine stopped with an error.")
        elif state == QTextToSpeech.State.Speaking:
            self._start_word_pacing()
        elif state == QTextToSpeech.State.Ready and self._read_waiting and self.is_reading():
            self._read_waiting = False
            self._learn_speed()
            self._stop_word_pacing()
            self._show_word(None)
            QTimer.singleShot(0, self, self._speak_next)

    def _words_on(self) -> bool:
        return app_settings().value(READ_WORDS_KEY, True, type=bool)

    def _engine_tracks_words(self) -> bool:
        from PySide6.QtTextToSpeech import QTextToSpeech

        tts = self._tts
        return tts is not None and bool(tts.engineCapabilities() & QTextToSpeech.Capability.WordByWordProgress)

    def _on_saying_word(self, word: str, _id: int, start: int, length: int) -> None:
        if self.is_reading() and self._words_on():
            self._show_word((start, length))

    def _show_word(self, span: tuple[int, int] | None) -> None:
        spoken = self._spoken
        if span == self._word_shown:
            return
        self._word_shown = span
        rects = []
        page = None
        if spoken is not None and span is not None:
            page, chars, _text, offsets = spoken
            first, length = span
            last = min(first + max(length, 1), len(offsets)) - 1
            if chars and 0 <= first <= last:
                rects, _ = rects_for_char_range(chars, offsets[first], min(offsets[last], len(chars) - 1))
        self.word_shown.emit(page, rects)

    def _speaking_speed(self) -> float:
        if self.chars_per_second > 0:
            return self.chars_per_second
        rate = app_settings().value(READ_RATE_KEY, 0.0, type=float)
        return WORDS_CPS * 2 ** max(-1.0, min(1.0, rate))

    def _start_word_pacing(self) -> None:
        self._word_clock = QElapsedTimer()
        self._word_clock.start()
        if self._engine_tracks_words() or not self._words_on():
            return
        if self._word_timer is None:
            self._word_timer = QTimer(self)
            self._word_timer.setInterval(WORD_TICK_MS)
            self._word_timer.timeout.connect(self._pace_word)
        self._pace_word()
        self._word_timer.start()

    def _stop_word_pacing(self) -> None:
        if self._word_timer is not None:
            self._word_timer.stop()

    def _pace_word(self) -> None:
        spoken = self._spoken
        if spoken is None or self._word_clock is None:
            return
        position = int(self._word_clock.elapsed() / 1000 * self._speaking_speed())
        self._show_word(word_at(spoken[2], position))

    def _learn_speed(self) -> None:
        spoken, clock = self._spoken, self._word_clock
        if spoken is None or clock is None or clock.elapsed() < 500:
            return
        measured = len(spoken[2]) / (clock.elapsed() / 1000)
        known = self.chars_per_second
        self.chars_per_second = measured if known <= 0 else 0.6 * known + 0.4 * measured
