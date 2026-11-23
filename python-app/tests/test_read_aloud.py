"""
Tests for read aloud (ReadAloud in riemann/ui/reader/mixins/read_aloud.py).

FakeSpeech stands in for QTextToSpeech: say() goes to Speaking and back to
Ready a moment later, which is all the reader relies on. With words=True it
reports each word through sayingWord, as engines with WordByWordProgress do;
without, the reader paces the word highlight itself. speak_ms sets how long a
sentence takes. Qt's own "mock"
engine was used before, but after other tests had run it could stay in
Speaking for good, so the test depended on test order.
"""

import os
import re
import time

import pytest
from PySide6.QtCore import QObject, QTimer, Signal

from riemann.ui.reader.mixins.read_aloud import collapse_spaces, split_sentences, word_at

CORPUS = os.path.join(os.path.dirname(__file__), "..", "..", "benchmarks", "corpus", "attention.pdf")


class FakeSpeech(QObject):
    stateChanged = Signal(object)
    sayingWord = Signal(str, int, int, int)

    def __init__(self, words=False, speak_ms=20):
        super().__init__()
        from PySide6.QtTextToSpeech import QTextToSpeech

        self.Capability = QTextToSpeech.Capability
        self.State = QTextToSpeech.State
        self._state = self.State.Ready
        self.said = []
        self.rate = None
        self.words = words
        self.speak_ms = speak_ms

    def engineCapabilities(self):
        return self.Capability.WordByWordProgress if self.words else self.Capability.None_

    def state(self):
        return self._state

    def _set(self, state):
        self._state = state
        self.stateChanged.emit(state)

    def say(self, text):
        self.said.append(text)
        self._set(self.State.Speaking)
        if self.words:
            for n, match in enumerate(re.finditer(r"\S+", text)):
                QTimer.singleShot(
                    5 + n * 30,
                    lambda m=match: self._state == self.State.Speaking
                    and self.sayingWord.emit(m.group(), 0, m.start(), m.end() - m.start()),
                )
        QTimer.singleShot(self.speak_ms, lambda: self._state == self.State.Speaking and self._set(self.State.Ready))

    def stop(self):
        if self._state == self.State.Speaking:
            self._set(self.State.Ready)

    def setRate(self, rate):
        self.rate = rate

    def availableVoices(self):
        return []

    def setVoice(self, voice):
        pass


def test_sentences_are_split_on_end_punctuation():
    text = "Attention is all. We use it! Does it work?  Yes “quoted.” tail"
    parts = [text[a:b] for a, b in split_sentences(text)]
    assert parts == ["Attention is all.", "We use it!", "Does it work?", "Yes “quoted.”", "tail"]


@pytest.mark.skipif(not os.path.exists(CORPUS), reason="benchmark corpus not downloaded")
def test_reads_sentence_by_sentence_with_highlight(qapp, monkeypatch):
    from PySide6.QtCore import QSettings

    from riemann.ui.reader.tab import ReaderTab

    store = QSettings("RiemannTest", "ReadAloud")
    store.setValue("reader/read_aloud_rate", 1.0)
    monkeypatch.setattr("riemann.ui.reader.mixins.read_aloud.app_settings", lambda: store)
    speech = FakeSpeech()
    monkeypatch.setattr("riemann.ui.reader.mixins.read_aloud.make_speech", lambda engine="": speech)

    def pump(seconds, until=lambda: False):
        end = time.time() + seconds
        while time.time() < end and not until():
            qapp.processEvents()
            time.sleep(0.01)

    tab = ReaderTab()
    tab.resize(1000, 800)
    tab.show()
    tab.load_document(os.path.abspath(CORPUS))
    pump(15, lambda: tab.current_doc is not None)
    pump(1)
    spoken = []
    tab.read_aloud.start(1)
    assert speech.rate == 1.0
    assert tab.read_aloud.is_reading()
    assert tab.btn_read_aloud.isChecked()
    first_queue = len(tab.read_aloud._read_queue)
    pump(10, lambda: len(tab.read_aloud._read_queue) < first_queue - 2)
    spoken.append(len(tab.read_aloud._read_queue))
    assert spoken[0] < first_queue
    assert len(speech.said) >= 2 and speech.said[0].strip()
    widget = tab.page_widgets[1]
    assert widget.spoken_rects
    tab.read_aloud.stop()
    assert not tab.read_aloud.is_reading()
    assert not widget.spoken_rects
    tab.cleanup()
    store.clear()


def test_read_aloud_works_without_a_reader(qapp, monkeypatch):
    from PySide6.QtCore import QRect, QSettings

    from riemann.ui.reader.mixins.read_aloud import ReadAloud

    store = QSettings("RiemannTest", "BareReadAloud")
    store.clear()
    store.setValue("accessibility/read_aloud_words", False)
    monkeypatch.setattr("riemann.ui.reader.mixins.read_aloud.app_settings", lambda: store)
    speech = FakeSpeech(speak_ms=20)
    monkeypatch.setattr("riemann.ui.reader.mixins.read_aloud.make_speech", lambda engine="": speech)

    def chars(text):
        return [(ch, QRect(i * 8, 10, 8, 12), 0) for i, ch in enumerate(text)]

    pages = {0: chars("First page. Second."), 1: chars("Last page.")}
    reader = ReadAloud(lambda page: pages[page], lambda: len(pages))
    shown, turned, needed, states = [], [], [], []
    reader.sentence_shown.connect(lambda page, rects: shown.append((page, len(rects))))
    reader.page_turned.connect(turned.append)
    reader.page_needed.connect(needed.append)
    reader.reading_changed.connect(states.append)

    reader.start(0)
    assert _pump(qapp, 5, lambda: not reader.is_reading())
    assert speech.said == ["First page.", "Second.", "Last page."]
    assert turned == [1] and needed == [0, 1]
    assert (0, 1) in shown and (1, 1) in shown and shown[-1] == (None, 0)
    assert states == [True, False]
    store.clear()


def test_spoken_text_maps_back_to_page_characters():
    text = "One  two\nthree. Four"
    spoken, offsets = collapse_spaces(text, 0, 15)
    assert spoken == "One two three."
    assert "".join(text[i] if not text[i].isspace() else " " for i in offsets) == spoken
    assert word_at(spoken, 0) == (0, 3)
    assert word_at(spoken, 3) == (4, 3)
    assert word_at(spoken, 9) == (8, 6)
    assert word_at(spoken, 99) is None


def _reader_reading(qapp, monkeypatch, speech, words_setting=True):
    from PySide6.QtCore import QSettings

    from riemann.ui.reader.tab import ReaderTab

    store = QSettings("RiemannTest", "ReadAloudWords")
    store.clear()
    store.setValue("accessibility/read_aloud_words", words_setting)
    monkeypatch.setattr("riemann.ui.reader.mixins.read_aloud.app_settings", lambda: store)
    monkeypatch.setattr("riemann.ui.reader.mixins.read_aloud.make_speech", lambda engine="": speech)
    tab = ReaderTab()
    tab.resize(1000, 800)
    tab.show()
    tab.load_document(os.path.abspath(CORPUS))
    _pump(qapp, 15, lambda: tab.current_doc is not None)
    _pump(qapp, 1)
    tab.read_aloud.start(1)
    return tab, store


def _pump(qapp, seconds, until=lambda: False):
    end = time.time() + seconds
    while time.time() < end and not until():
        qapp.processEvents()
        time.sleep(0.005)
    return until()


def _word_under_highlight(tab):
    _page, chars, text, offsets = tab.read_aloud._spoken
    start, length = tab.read_aloud._word_shown
    return text[start:start + length], "".join(chars[offsets[i]][0] for i in range(start, start + length))


@pytest.mark.skipif(not os.path.exists(CORPUS), reason="benchmark corpus not downloaded")
@pytest.mark.parametrize("engine_words", [True, False])
def test_the_word_being_spoken_is_highlighted_on_the_page(qapp, monkeypatch, engine_words):
    """Engines that report words drive the highlight; for the rest it is paced by speaking speed."""
    speech = FakeSpeech(words=engine_words, speak_ms=3000)
    tab, store = _reader_reading(qapp, monkeypatch, speech)
    if not engine_words:
        tab.read_aloud.chars_per_second = 40.0
    widget = tab.page_widgets[1]
    seen = []

    def advanced():
        if tab.read_aloud._word_shown is not None and getattr(widget, "word_rects", None):
            seen.append(_word_under_highlight(tab))
        return len({w for w, _ in seen}) >= 3

    assert _pump(qapp, 5, advanced)
    for spoken_word, page_word in seen:
        assert spoken_word == page_word
    sentence = widget.spoken_rects
    for rect in widget.word_rects:
        assert any(line.intersects(rect) for line in sentence)
    tab.read_aloud.stop()
    assert not getattr(widget, "word_rects", None)
    tab.cleanup()
    store.clear()


@pytest.mark.skipif(not os.path.exists(CORPUS), reason="benchmark corpus not downloaded")
def test_word_highlight_follows_its_setting(qapp, monkeypatch):
    speech = FakeSpeech(words=True, speak_ms=400)
    tab, store = _reader_reading(qapp, monkeypatch, speech, words_setting=False)
    widget = tab.page_widgets[1]
    _pump(qapp, 0.3)
    assert widget.spoken_rects
    assert not getattr(widget, "word_rects", None)
    tab.read_aloud.stop()
    tab.cleanup()
    store.clear()


@pytest.mark.skipif(not os.path.exists(CORPUS), reason="benchmark corpus not downloaded")
def test_pacing_learns_the_speaking_speed_from_finished_sentences(qapp, monkeypatch):
    speech = FakeSpeech(words=False, speak_ms=600)
    tab, store = _reader_reading(qapp, monkeypatch, speech)
    first = speech.said
    assert _pump(qapp, 5, lambda: len(first) >= 3)
    expected = len(first[0]) / 0.6
    assert 0.5 * expected < tab.read_aloud.chars_per_second < 1.5 * expected
    tab.read_aloud.stop()
    tab.cleanup()
    store.clear()
