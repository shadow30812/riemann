import time

from PySide6.QtCore import QUrl
from PySide6.QtWebSockets import QWebSocket

from riemann.core.captions_server import CaptionsServer


def _connect(qapp, url, until, seconds=10.0):
    ws = QWebSocket()
    events = []
    ws.connected.connect(lambda: events.append("connected"))
    ws.disconnected.connect(lambda: events.append("disconnected"))
    ws.open(QUrl(url))
    end = time.time() + seconds
    while time.time() < end and not until(events):
        qapp.processEvents()
        time.sleep(0.01)
    return ws, events


def test_captions_server_requires_its_token(qapp):
    server = CaptionsServer()
    try:
        base = f"ws://127.0.0.1:{server.port}"
        _, events = _connect(qapp, base + "/?token=wrong", lambda ev: "disconnected" in ev)
        assert "disconnected" in events
        assert server.clients == []

        ws, events = _connect(qapp, f"{base}/?token={server.token}", lambda ev: "connected" in ev and server.clients)
        end = time.time() + 0.3
        while time.time() < end:
            qapp.processEvents()
            time.sleep(0.01)
        assert events == ["connected"]
        assert len(server.clients) == 1
        ws.close()
    finally:
        server.stop()
