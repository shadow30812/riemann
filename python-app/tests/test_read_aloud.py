"""
Tests for read aloud (riemann/ui/reader/mixins/read_aloud.py).

FakeSpeech stands in for QTextToSpeech: say() goes to Speaking and back to
Ready a moment later, which is all the reader relies on. Qt's own "mock"
engine was used before, but after other tests had run it could stay in
Speaking for good, so the test depended on test order.
"""

import os
import time

import pytest

from PySide6.QtCore import QObject, QTimer, Signal

from riemann.ui.reader.mixins.read_aloud import split_sentences

CORPUS = os.path.join(os.path.dirname(__file__), "..", "..", "benchmarks", "corpus", "attention.pdf")


class FakeSpeech(QObject):
    stateChanged = Signal(object)

    def __init__(self):
        super().__init__()
        from PySide6.QtTextToSpeech import QTextToSpeech

        self.State = QTextToSpeech.State
        self._state = self.State.Ready
        self.said = []
        self.rate = None

    def state(self):
        return self._state

    def _set(self, state):
        self._state = state
        self.stateChanged.emit(state)

    def say(self, text):
        self.said.append(text)
        self._set(self.State.Speaking)
        QTimer.singleShot(20, lambda: self._state == self.State.Speaking and self._set(self.State.Ready))

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
    tab.start_read_aloud(page=1)
    assert speech.rate == 1.0
    assert tab.is_reading_aloud()
    assert tab.btn_read_aloud.isChecked()
    first_queue = len(tab._read_queue)
    pump(10, lambda: len(tab._read_queue) < first_queue - 2)
    spoken.append(len(tab._read_queue))
    assert spoken[0] < first_queue
    assert len(speech.said) >= 2 and speech.said[0].strip()
    widget = tab.page_widgets[1]
    assert widget.spoken_rects
    tab.stop_read_aloud()
    assert not tab.is_reading_aloud()
    assert not widget.spoken_rects
    tab.cleanup()
    store.clear()
