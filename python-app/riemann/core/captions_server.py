import numpy as np
from PySide6.QtCore import QByteArray, QObject, QThread, Signal, Slot
from PySide6.QtNetwork import QHostAddress
from PySide6.QtWebSockets import QWebSocket, QWebSocketServer


class WhisperWorker(QThread):
    transcription_ready = Signal(str, object)
    chunk_received = Signal(bytes, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.chunk_received.connect(self._process_chunk)

    @Slot(bytes, object)
    def _process_chunk(self, byte_data: bytes, client_ws: object):
        """Processes the 32-bit float audio array using faster-whisper."""
        try:
            from riemann.core import captions

            audio_array = np.frombuffer(byte_data, dtype=np.float32)
            text = captions.process_audio_chunk(audio_array)

            if text and text.strip():
                self.transcription_ready.emit(text.strip(), client_ws)
        except ImportError:
            print("[ERROR] faster-whisper is not installed.")
        except Exception as e:
            print(f"[ERROR] Caption processing failed: {e}")

    def run(self):
        """Start the internal Qt event loop for this background thread."""
        self.exec()


class CaptionsServer(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.server = QWebSocketServer(
            "Riemann Captions Engine", QWebSocketServer.SslMode.NonSecureMode, self
        )
        self.clients = []

        self.worker = WhisperWorker()
        self.worker.start()
        self.worker.transcription_ready.connect(self.send_transcription)

        if self.server.listen(QHostAddress.SpecialAddress.LocalHost):
            self.port = self.server.serverPort()
            print(f"[Riemann] Captions Server running natively on port {self.port}")
            self.server.newConnection.connect(self.on_new_connection)
        else:
            print("[ERROR] Failed to start native PySide6 WebSocket Server.")
            self.port = None

    @Slot()
    def on_new_connection(self):
        client = self.server.nextPendingConnection()
        self.clients.append(client)
        client.binaryMessageReceived.connect(
            lambda data, c=client: self.on_binary_message(data, c)
        )
        client.disconnected.connect(lambda c=client: self.on_disconnected(c))

    def on_binary_message(self, message: QByteArray, client: QWebSocket):
        self.worker.chunk_received.emit(message.data(), client)

    def on_disconnected(self, client: QWebSocket):
        if client in self.clients:
            self.clients.remove(client)
        client.deleteLater()

    @Slot(str, object)
    def send_transcription(self, text: str, client: QWebSocket):
        if client in self.clients:
            client.sendTextMessage(text)

    def stop(self):
        self.server.close()
        self.worker.quit()
        self.worker.wait()
