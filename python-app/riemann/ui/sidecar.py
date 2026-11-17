"""
AI assistant panel.

A chat about the open document, shown beside it. Several providers are
supported (Anthropic, OpenAI, Google, OpenRouter and a local Ollama server);
each one's model list is fetched from the provider, so new models show up
without an update. Answers cite pages as [p. N], which link back into the
document.

Notes:

showEvent: The panel exists from startup; ask the provider for models only once
someone opens it, not on every launch.

_on_chunk: The answer is drawn again at most once per render interval while it
streams. Restarting the timer on every chunk drew nothing until the answer
ended, and drawing on every chunk re-rendered the whole Markdown each time.
"""

import html
import json
import os
import re
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional, Tuple

from PySide6.QtCore import QObject, QSettings, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .palette import tinted_icon, tokens
from .reader.utils import render_markdown_safely
from ..core.log import log
from ..core.settings import app_settings
from ..core.endpoints import (
    ANTHROPIC_API,
    GEMINI_API,
    OLLAMA_DEFAULT_URL,
    OPENAI_API,
    OPENROUTER_API,
)


@dataclass(frozen=True)
class Provider:
    key: str
    label: str
    env: Tuple[str, ...] = ()
    needs_key: bool = True
    fallback_models: Tuple[str, ...] = field(default_factory=tuple)


PROVIDERS: Dict[str, Provider] = {
    p.key: p
    for p in (
        Provider(
            "anthropic",
            "Claude (Anthropic)",
            ("ANTHROPIC_API_KEY",),
            fallback_models=("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-4-5"),
        ),
        Provider("openai", "OpenAI", ("OPENAI_API_KEY",)),
        Provider("google", "Gemini (Google)", ("GEMINI_API_KEY", "GOOGLE_API_KEY")),
        Provider("openrouter", "OpenRouter (many providers)", ("OPENROUTER_API_KEY",)),
        Provider("ollama", "Ollama (local)", needs_key=False),
    )
}

CONTEXT_CHARS = 150_000

SYSTEM_PROMPT = (
    "You are the reading assistant inside Riemann, a PDF reader. The user is reading "
    "the document below and asks about it. Ground every answer in the document and "
    "say plainly when it does not cover something. Cite the pages you rely on as "
    "[p. N] (for example [p. 4] or [p. 4, 7]). Use Markdown; keep answers focused."
)


def _settings() -> QSettings:
    return app_settings()


def keys_file() -> str:
    """Where the API keys live: ai.env in Riemann's per-user config folder."""
    override = os.environ.get("RIEMANN_AI_ENV")
    if override:
        return override
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "Riemann", "ai.env")


def _tighten(path: str, mode: int) -> None:
    if os.name != "nt" and os.stat(path).st_mode & 0o777 != mode:
        os.chmod(path, mode)


def read_keys_file() -> Dict[str, str]:
    """
    Parses ai.env (NAME=value lines, # comments). If someone loosened its
    permissions, they are put back to owner-only before the keys are used.
    """
    path = keys_file()
    try:
        _tighten(path, 0o600)
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return {}
    values = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[name] = value
    return values


def write_keys_file(values: Dict[str, str]) -> None:
    """
    Replaces ai.env atomically. The folder is created owner-only (0700) and
    the file is born 0600, so the keys are never readable by other users,
    not even for the moment between writing and renaming.
    """
    path = keys_file()
    folder = os.path.dirname(path)
    os.makedirs(folder, mode=0o700, exist_ok=True)
    _tighten(folder, 0o700)
    lines = ["# Riemann AI assistant keys. Owner-only file; do not share or commit it."]
    lines += [f"{name}={value}" for name, value in values.items() if value]
    tmp = f"{path}.{os.getpid()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def _migrate_settings_keys() -> None:
    """Moves keys saved by earlier builds out of the plain settings file."""
    settings = _settings()
    old = {
        PROVIDERS[p].env[0]: str(settings.value(f"ai/keys/{p}", "") or "")
        for p in PROVIDERS
        if PROVIDERS[p].env and settings.contains(f"ai/keys/{p}")
    }
    if not old:
        return
    values = read_keys_file()
    for name, value in old.items():
        if value and not values.get(name):
            values[name] = value
    write_keys_file(values)
    settings.remove("ai/keys")
    settings.sync()


def saved_key(provider: str) -> str:
    stored = read_keys_file()
    for name in PROVIDERS[provider].env:
        if stored.get(name):
            return stored[name]
    return ""


def api_key(provider: str) -> str:
    """The key for provider: a real environment variable wins over ai.env."""
    for name in PROVIDERS[provider].env:
        if os.environ.get(name):
            return os.environ[name]
    return saved_key(provider)


def ollama_url() -> str:
    return str(_settings().value("ai/ollama_url", OLLAMA_DEFAULT_URL) or OLLAMA_DEFAULT_URL).rstrip("/")


class ProviderError(Exception):
    pass


def _request(url: str, headers: Dict[str, str], body: Optional[dict] = None, timeout: float = 30):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    req.add_header("Content-Type", "application/json")
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            payload = json.loads(e.read().decode("utf-8", "replace"))
            err = payload.get("error", payload)
            detail = err.get("message") if isinstance(err, dict) else str(err)
        except Exception:
            log.debug("_request failed", exc_info=True)
        raise ProviderError(f"{e.code} {e.reason}" + (f": {detail}" if detail else "")) from None
    except urllib.error.URLError as e:
        raise ProviderError(f"Could not connect: {e.reason}") from None


def _headers(provider: str, key: str) -> Dict[str, str]:
    if provider == "anthropic":
        return {"x-api-key": key, "anthropic-version": "2023-06-01"}
    if provider == "google":
        return {"x-goog-api-key": key}
    if provider in ("openai", "openrouter"):
        headers = {"Authorization": f"Bearer {key}"}
        if provider == "openrouter":
            headers["X-Title"] = "Riemann"
        return headers
    return {}


_OPENAI_SKIP = ("audio", "realtime", "transcribe", "tts", "image", "embedding", "search", "moderation", "instruct", "dall-e", "whisper", "davinci", "babbage")


def list_models(provider: str, key: str) -> List[str]:
    """Asks the provider which chat models this key can use, newest names first."""
    if provider == "anthropic":
        with _request(f"{ANTHROPIC_API}/models?limit=100", _headers(provider, key)) as r:
            return [m["id"] for m in json.load(r).get("data", [])]
    if provider == "openai":
        with _request(f"{OPENAI_API}/models", _headers(provider, key)) as r:
            ids = [m["id"] for m in json.load(r).get("data", [])]
        chat = [i for i in ids if re.match(r"^(gpt-|o\d|chatgpt)", i) and not any(s in i for s in _OPENAI_SKIP)]
        return sorted(chat, reverse=True)
    if provider == "google":
        with _request(
            f"{GEMINI_API}/models?pageSize=1000", _headers(provider, key)
        ) as r:
            models = json.load(r).get("models", [])
        names = [
            m["name"].split("/", 1)[-1]
            for m in models
            if "generateContent" in m.get("supportedGenerationMethods", []) and "gemini" in m.get("name", "")
        ]
        return sorted(names, reverse=True)
    if provider == "openrouter":
        with _request(f"{OPENROUTER_API}/models", {}) as r:
            return [m["id"] for m in json.load(r).get("data", [])]
    if provider == "ollama":
        with _request(f"{ollama_url()}/api/tags", {}, timeout=5) as r:
            return [m["name"] for m in json.load(r).get("models", [])]
    return []


def _sse_events(response) -> Iterator[dict]:
    for raw in response:
        line = raw.decode("utf-8", "replace").strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            return
        try:
            yield json.loads(data)
        except ValueError:
            continue


def stream_chat(
    provider: str, key: str, model: str, system: str, messages: List[Dict[str, str]]
) -> Iterator[str]:
    """Sends the conversation and yields the reply as it arrives."""
    if provider == "anthropic":
        body = {"model": model, "max_tokens": 4096, "system": system, "messages": messages, "stream": True}
        with _request(f"{ANTHROPIC_API}/messages", _headers(provider, key), body, timeout=120) as r:
            for event in _sse_events(r):
                if event.get("type") == "content_block_delta":
                    yield event.get("delta", {}).get("text", "")
                elif event.get("type") == "error":
                    raise ProviderError(event.get("error", {}).get("message", "error"))
        return

    if provider in ("openai", "openrouter"):
        base = OPENAI_API if provider == "openai" else OPENROUTER_API
        body = {"model": model, "messages": [{"role": "system", "content": system}] + messages, "stream": True}
        with _request(f"{base}/chat/completions", _headers(provider, key), body, timeout=120) as r:
            for event in _sse_events(r):
                for choice in event.get("choices", []):
                    yield (choice.get("delta") or {}).get("content") or ""
        return

    if provider == "google":
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [
                {"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]}
                for m in messages
            ],
        }
        url = f"{GEMINI_API}/models/{model}:streamGenerateContent?alt=sse"
        with _request(url, _headers(provider, key), body, timeout=120) as r:
            for event in _sse_events(r):
                for cand in event.get("candidates", []):
                    for part in cand.get("content", {}).get("parts", []):
                        yield part.get("text", "")
        return

    if provider == "ollama":
        body = {"model": model, "messages": [{"role": "system", "content": system}] + messages, "stream": True}
        with _request(f"{ollama_url()}/api/chat", {}, body, timeout=300) as r:
            for raw in r:
                try:
                    event = json.loads(raw)
                except ValueError:
                    continue
                if event.get("error"):
                    raise ProviderError(event["error"])
                yield event.get("message", {}).get("content", "")
        return

    raise ProviderError(f"Unknown provider {provider}")


def document_context(doc: Any, current_page: int, budget: int = CONTEXT_CHARS) -> str:
    """
    The document's text with page markers. Long documents are cut to the
    pages nearest the one being read, so the request stays within budget.
    """
    count = getattr(doc, "page_count", 0)
    order = sorted(range(count), key=lambda i: (abs(i - current_page), i))
    chosen: Dict[int, str] = {}
    used = 0
    for i in order:
        try:
            text = doc.get_page_text(i).strip()
        except Exception:
            continue
        if used + len(text) > budget and chosen:
            break
        chosen[i] = text
        used += len(text)
    parts = [f"[Page {i + 1}]\n{chosen[i]}" for i in sorted(chosen)]
    note = "" if len(chosen) == count else f"(Only pages near page {current_page + 1} are included.)\n\n"
    return note + "\n\n".join(parts)


_CITATION = re.compile(r"\[(?:pp?\.|pages?)\s*([0-9][0-9,\s–-]*)\]", re.IGNORECASE)


def link_citations(text: str) -> str:
    """Turns [p. 3, 5] into Markdown links that jump to those pages."""

    def repl(match: re.Match) -> str:
        nums = re.findall(r"\d+", match.group(1))
        links = ", ".join(f"[{n}](page:{n})" for n in nums)
        return f"[p. {links}]" if nums else match.group(0)

    return _CITATION.sub(repl, text)


class _Bridge(QObject):
    chunk = Signal(int, str)
    finished = Signal(int, str)
    models = Signal(str, list, str)


class KeysDialog(QDialog):
    """Edits the saved API keys and the Ollama address."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("AI Assistant Keys")
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        note = QLabel(
            f"Keys are saved in {keys_file()}, readable only by your user account. "
            "An environment variable (for example ANTHROPIC_API_KEY) takes precedence."
        )
        note.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QFormLayout()
        self.fields: Dict[str, QLineEdit] = {}
        for p in PROVIDERS.values():
            if not p.needs_key:
                continue
            edit = QLineEdit(saved_key(p.key))
            edit.setEchoMode(QLineEdit.EchoMode.Password)
            edit.setPlaceholderText(" or ".join(p.env))
            form.addRow(p.label + ":", edit)
            self.fields[p.key] = edit
        self.ollama = QLineEdit(ollama_url())
        form.addRow("Ollama address:", self.ollama)
        layout.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _save(self) -> None:
        values = read_keys_file()
        for key, edit in self.fields.items():
            for name in PROVIDERS[key].env:
                values.pop(name, None)
            values[PROVIDERS[key].env[0]] = edit.text().strip()
        try:
            write_keys_file(values)
        except OSError as e:
            QMessageBox.warning(self, "AI Assistant Keys", f"Could not save the keys:\n{e}")
            return
        _settings().setValue("ai/ollama_url", self.ollama.text().strip() or OLLAMA_DEFAULT_URL)
        self.accept()


class AiSidecarPanel(QWidget):
    """Chat about the active document, docked beside it."""

    page_jump_requested = Signal(int)
    close_requested = Signal()

    def __init__(self, parent=None, dark_mode: bool = False):
        super().__init__(parent)
        self.dark_mode = dark_mode
        self.reader = None
        self.messages: List[Dict[str, str]] = []
        self._pending = ""
        self._request_id = 0
        self._busy = False
        self._context_cache: Dict[Tuple[str, int], str] = {}
        self._models_loaded: Dict[str, List[str]] = {}
        try:
            _migrate_settings_keys()
        except OSError as e:
            print(f"Could not move AI keys out of the settings file: {e}")

        self._bridge = _Bridge(self)
        self._bridge.chunk.connect(self._on_chunk)
        self._bridge.finished.connect(self._on_finished)
        self._bridge.models.connect(self._on_models)
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(60)
        self._render_timer.timeout.connect(self._render)

        self.setMinimumWidth(320)
        self._build()
        self.update_theme(dark_mode)
        self._select_provider(str(_settings().value("ai/provider", "anthropic")))
        self._render()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.header = QFrame(self)
        self.header.setObjectName("AiHeader")
        head = QVBoxLayout(self.header)
        head.setContentsMargins(12, 8, 8, 8)
        head.setSpacing(6)

        row = QHBoxLayout()
        self.lbl_title = QLabel("AI Assistant")
        self.lbl_title.setObjectName("AiTitle")
        row.addWidget(self.lbl_title)
        row.addStretch()
        self.btn_new = QToolButton()
        self.btn_new.setToolTip("New chat")
        self.btn_new.clicked.connect(self.clear_chat)
        self.btn_keys = QToolButton()
        self.btn_keys.setToolTip("API keys and Ollama address")
        self.btn_keys.clicked.connect(self._edit_keys)
        self.btn_close = QToolButton()
        self.btn_close.setToolTip("Close (Ctrl+Alt+A)")
        self.btn_close.clicked.connect(self.close_requested.emit)
        for btn in (self.btn_new, self.btn_keys, self.btn_close):
            btn.setAutoRaise(True)
            btn.setIconSize(QSize(16, 16))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            row.addWidget(btn)
        head.addLayout(row)

        pick = QHBoxLayout()
        pick.setSpacing(6)
        self.combo_provider = QComboBox()
        for p in PROVIDERS.values():
            self.combo_provider.addItem(p.label, p.key)
        self.combo_provider.currentIndexChanged.connect(
            lambda _: self._select_provider(self.combo_provider.currentData())
        )
        self.combo_model = QComboBox()
        self.combo_model.setEditable(True)
        self.combo_model.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.combo_model.setMinimumContentsLength(14)
        self.combo_model.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.combo_model.currentTextChanged.connect(self._remember_model)
        self.btn_refresh = QToolButton()
        self.btn_refresh.setToolTip("Reload the model list")
        self.btn_refresh.setAutoRaise(True)
        self.btn_refresh.clicked.connect(lambda: self._load_models(force=True))
        pick.addWidget(self.combo_provider, 1)
        pick.addWidget(self.combo_model, 1)
        pick.addWidget(self.btn_refresh)
        head.addLayout(pick)

        self.lbl_doc = QLabel()
        self.lbl_doc.setObjectName("AiDoc")
        head.addWidget(self.lbl_doc)
        layout.addWidget(self.header)

        self.view = QTextBrowser()
        self.view.setOpenLinks(False)
        self.view.anchorClicked.connect(self._on_link)
        layout.addWidget(self.view, 1)

        chips = QHBoxLayout()
        chips.setContentsMargins(12, 6, 12, 0)
        chips.setSpacing(6)
        for label, prompt in (
            ("Summarize", "Summarize this document."),
            ("Key points", "List the key points and findings, with page references."),
            ("Explain this page", "Explain the page I am reading in simpler terms."),
        ):
            chip = QPushButton(label)
            chip.setObjectName("AiChip")
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.clicked.connect(lambda _=False, p=prompt: self.ask(p))
            chips.addWidget(chip)
        chips.addStretch()
        layout.addLayout(chips)

        box = QFrame()
        box.setObjectName("AiInput")
        box_layout = QHBoxLayout(box)
        box_layout.setContentsMargins(10, 6, 6, 6)
        self.input = QPlainTextEdit()
        self.input.setPlaceholderText("Ask about this document…")
        self.input.setFixedHeight(64)
        self.input.installEventFilter(self)
        self.btn_send = QToolButton()
        self.btn_send.setToolTip("Send (Enter)")
        self.btn_send.setIconSize(QSize(18, 18))
        self.btn_send.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_send.clicked.connect(self._send_or_stop)
        box_layout.addWidget(self.input, 1)
        box_layout.addWidget(self.btn_send, 0, Qt.AlignmentFlag.AlignBottom)
        outer = QVBoxLayout()
        outer.setContentsMargins(12, 8, 12, 12)
        outer.addWidget(box)
        layout.addLayout(outer)

        QShortcut(QKeySequence("Ctrl+L"), self, activated=self.input.setFocus)

    def eventFilter(self, obj, event) -> bool:
        if obj is self.input and event.type() == event.Type.KeyPress:
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (
                event.modifiers() & Qt.KeyboardModifier.ShiftModifier
            ):
                self._send_or_stop()
                return True
        return super().eventFilter(obj, event)

    def update_theme(self, dark: bool) -> None:
        self.dark_mode = dark
        t = tokens(dark)
        self.setStyleSheet(
            f"AiSidecarPanel {{ background: {t['canvas']}; border-left: 1px solid {t['border']}; }}"
            f"#AiHeader {{ background: {t['surface']}; border-bottom: 1px solid {t['border']}; }}"
            f"#AiTitle {{ color: {t['text']}; font-weight: 600; font-size: 13px; }}"
            f"#AiDoc {{ color: {t['faint']}; font-size: 11px; }}"
            f"QComboBox {{ background: {t['canvas']}; color: {t['text']}; border: 1px solid {t['border']}; border-radius: 6px; padding: 3px 6px; }}"
            f"QTextBrowser {{ background: {t['canvas']}; color: {t['text']}; border: none; padding: 8px; }}"
            f"#AiChip {{ background: {t['surface']}; color: {t['muted']}; border: 1px solid {t['border']}; border-radius: 12px; padding: 3px 10px; font-size: 11px; }}"
            f"#AiChip:hover {{ border-color: {t['accent']}; color: {t['text']}; }}"
            f"#AiInput {{ background: {t['surface']}; border: 1px solid {t['border_strong']}; border-radius: 14px; }}"
            f"QPlainTextEdit {{ background: transparent; color: {t['text']}; border: none; font-size: 13px; }}"
            f"QToolButton {{ border: none; border-radius: 6px; padding: 3px; }}"
            f"QToolButton:hover {{ background: {t['raised']}; }}"
        )
        self.btn_new.setIcon(tinted_icon("plus", t["muted"], 16))
        self.btn_keys.setIcon(tinted_icon("lock", t["muted"], 16))
        self.btn_close.setIcon(tinted_icon("x", t["muted"], 16))
        self.btn_refresh.setIcon(tinted_icon("rotate-cw", t["muted"], 16))
        self._update_send_button()
        self._render()

    def _update_send_button(self) -> None:
        t = tokens(self.dark_mode)
        self.btn_send.setIcon(tinted_icon("circle-stop" if self._busy else "send", t["accent"], 18))
        self.btn_send.setToolTip("Stop" if self._busy else "Send (Enter)")

    def set_document(self, reader: Any) -> None:
        """Points the assistant at the reader tab whose document it should discuss."""
        self.reader = reader
        path = getattr(reader, "current_path", "") or ""
        has_doc = bool(path) and getattr(reader, "current_doc", None) is not None
        self.lbl_doc.setText(
            f"About: {os.path.basename(path)}" if has_doc else "Open a PDF to ask about it."
        )
        self.lbl_doc.setToolTip(path)

    def _select_provider(self, provider: str) -> None:
        if provider not in PROVIDERS:
            provider = "anthropic"
        idx = self.combo_provider.findData(provider)
        if idx != self.combo_provider.currentIndex():
            self.combo_provider.blockSignals(True)
            self.combo_provider.setCurrentIndex(idx)
            self.combo_provider.blockSignals(False)
        _settings().setValue("ai/provider", provider)
        self._fill_models(self._models_loaded.get(provider) or list(PROVIDERS[provider].fallback_models))
        if self.isVisible():
            self._load_models()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._load_models()

    def _provider(self) -> str:
        return self.combo_provider.currentData()

    def _fill_models(self, models: List[str]) -> None:
        provider = self._provider()
        saved = str(_settings().value(f"ai/model/{provider}", "") or "")
        self.combo_model.blockSignals(True)
        self.combo_model.clear()
        self.combo_model.addItems(models)
        if saved:
            self.combo_model.setCurrentText(saved)
        self.combo_model.blockSignals(False)

    def _remember_model(self, name: str) -> None:
        if name:
            _settings().setValue(f"ai/model/{self._provider()}", name)

    def _load_models(self, force: bool = False) -> None:
        provider = self._provider()
        if provider in self._models_loaded and not force:
            return
        key = api_key(provider)
        if PROVIDERS[provider].needs_key and not key and provider != "openrouter":
            return
        bridge = self._bridge

        def work():
            try:
                models, error = list_models(provider, key), ""
            except Exception as e:
                models, error = [], str(e)
            try:
                bridge.models.emit(provider, models, error)
            except RuntimeError:
                pass

        threading.Thread(target=work, name="riemann-ai-models", daemon=True).start()

    def _on_models(self, provider: str, models: list, error: str) -> None:
        if models:
            self._models_loaded[provider] = models
            if provider == self._provider():
                self._fill_models(models)
        elif error and provider == self._provider():
            self.messages.append(
                {"role": "error", "content": f"Could not list {PROVIDERS[provider].label} models: {error}"}
            )
            self._render()

    def _edit_keys(self) -> None:
        if KeysDialog(self).exec():
            self._models_loaded.clear()
            self._load_models(force=True)

    def _notice(self, text: str) -> None:
        self.messages.append({"role": "notice", "content": text})
        self._render()

    def clear_chat(self) -> None:
        self._request_id += 1
        self._busy = False
        self._update_send_button()
        self.messages = []
        self._pending = ""
        self._render()

    def _send_or_stop(self) -> None:
        if self._busy:
            self._request_id += 1
            self._on_finished(self._request_id, "")
            return
        text = self.input.toPlainText().strip()
        if text:
            self.input.clear()
            self.ask(text)

    def ask(self, question: str) -> None:
        """Sends question about the current document to the selected model."""
        if self._busy:
            return
        provider = self._provider()
        model = self.combo_model.currentText().strip()
        key = api_key(provider)
        reader = self.reader
        doc = getattr(reader, "current_doc", None)
        if doc is None or not getattr(reader, "current_path", None):
            self._notice("Open a PDF first; the assistant answers questions about it.")
            return
        if PROVIDERS[provider].needs_key and not key:
            self._notice(
                f"Add a {PROVIDERS[provider].label} API key with the key button above "
                f"(or set {PROVIDERS[provider].env[0]})."
            )
            return
        if not model:
            self._notice("Choose a model first.")
            return

        page = getattr(reader, "current_page_index", 0)
        self.messages.append({"role": "user", "content": question})
        self._pending = ""
        self._busy = True
        self._request_id += 1
        request_id = self._request_id
        self._update_send_button()
        self._render()

        history = [m for m in self.messages if m["role"] in ("user", "assistant")]
        cache_key = (reader.current_path, page // 10)
        cached = self._context_cache.get(cache_key)
        bridge = self._bridge
        name = os.path.basename(reader.current_path)

        def work():
            try:
                context = cached or document_context(doc, page)
                self._context_cache[cache_key] = context
                system = (
                    f"{SYSTEM_PROMPT}\n\nThe user is on page {page + 1}.\n\n"
                    f"<document name=\"{name}\">\n{context}\n</document>"
                )
                for piece in stream_chat(provider, key, model, system, history):
                    if request_id != self._request_id:
                        return
                    if piece:
                        bridge.chunk.emit(request_id, piece)
                bridge.finished.emit(request_id, "")
            except RuntimeError:
                pass
            except Exception as e:
                try:
                    bridge.finished.emit(request_id, str(e) or type(e).__name__)
                except RuntimeError:
                    pass

        threading.Thread(target=work, name="riemann-ai-chat", daemon=True).start()

    def _on_chunk(self, request_id: int, piece: str) -> None:
        if request_id == self._request_id:
            self._pending += piece
            if not self._render_timer.isActive():
                self._render_timer.start()

    def _on_finished(self, request_id: int, error: str) -> None:
        if request_id != self._request_id or not self._busy:
            return
        self._busy = False
        if self._pending:
            self.messages.append({"role": "assistant", "content": self._pending})
        self._pending = ""
        if error:
            self.messages.append({"role": "error", "content": f"Request failed: {error}"})
        self._update_send_button()
        self._render()

    def _on_link(self, url: QUrl) -> None:
        if url.scheme() == "page":
            try:
                self.page_jump_requested.emit(int(url.path() or url.toString().split(":", 1)[1]) - 1)
            except ValueError:
                pass

    def _render(self) -> None:
        t = tokens(self.dark_mode)
        parts = [
            f"<style>a {{ color: {t['accent']}; text-decoration: none; }}"
            f" p, li {{ line-height: 140%; }} pre, code {{ background: {t['surface']}; }}</style>"
        ]
        shown = list(self.messages)
        if self._busy:
            shown.append({"role": "assistant", "content": self._pending or "_Thinking…_"})
        if not shown:
            parts.append(
                f"<p style='color:{t['faint']}'>Ask anything about the open document. "
                "Answers cite pages; click a page number to jump there.</p>"
            )
        for m in shown:
            if m["role"] == "user":
                parts.append(
                    f"<table width='100%' cellspacing='0' cellpadding='8' style='margin-top:10px'>"
                    f"<tr><td style='background:{t['surface']};"
                    f" border-radius:8px; color:{t['text']}'><b>You</b><br>"
                    f"{html.escape(m['content']).replace(chr(10), '<br>')}</td></tr></table>"
                )
            elif m["role"] == "assistant":
                body = render_markdown_safely(link_citations(m["content"]), schemes=("page",))
                parts.append(f"<div style='margin-top:10px; color:{t['text']}'>{body}</div>")
            else:
                color = t["danger"] if m["role"] == "error" else t["muted"]
                parts.append(f"<p style='margin-top:10px; color:{color}'>{html.escape(m['content'])}</p>")
        bar = self.view.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 4
        self.view.setHtml("".join(parts))
        if at_bottom:
            bar.setValue(bar.maximum())
