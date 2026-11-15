"""
The Settings tab.

Every change applies as soon as it is made, as in a web browser's settings
page; there is no OK or Cancel. Changes that only take effect after a restart
(display size, window snapping on Wayland) say so next to the control instead
of in a dialog. The window's own methods do the work (toggle_ui_theme,
set_vertical_tabs, toggle_auto_pdf...), so a change made here and one made
from a menu or shortcut behave the same.

Sections: Appearance, Documents, Browser, Site permissions, AI and OCR,
Privacy and data, About.
"""

import importlib.metadata

from PySide6.QtCore import QStandardPaths, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
)

from ...core import permissions
from ...core.dependencies import DependenciesDialog
from ...core.log import log_folder
from ...core.settings import (
    AI_TAGS_KEY,
    DEFAULT_POPULAR_SITES,
    OCR_LANGUAGES_KEY,
    POPULAR_SITES_KEY,
    app_settings,
    set_string_list,
    string_list,
)
from ...core.window_snap import is_wayland_session, snapping_default
from .base import SectionedSystemTab, card, row_container, setting_row
from ..reader.mixins.read_aloud import NO_ENGINE, READ_RATE_KEY, READ_VOICE_KEY, make_speech
from .widgets import PermissionsEditor, StringListEditor, ToolbarLayoutEditor, VoicePicker


def app_version() -> str:
    try:
        return importlib.metadata.version("riemann")
    except importlib.metadata.PackageNotFoundError:
        return ""


class SettingsTab(SectionedSystemTab):
    page = "settings"
    title = "Settings"
    subtitle = "Changes apply right away."
    icon_name = "settings"

    def build_sections(self) -> None:
        self.settings = app_settings()
        self._appearance(self.add_section("appearance", "Appearance", "palette"))
        self._documents(self.add_section("documents", "Documents", "file-text"))
        self._browser(self.add_section("browser", "Browser", "browser"))
        self._permissions(self.add_section("permissions", "Site permissions", "shield"))
        self._ai(self.add_section("ai", "AI and OCR", "sparkles"))
        self._privacy(self.add_section("privacy", "Privacy and data", "lock"))
        self._about(self.add_section("about", "About", "circle-question-mark"))

    def _check(self, checked: bool, on_change) -> QCheckBox:
        box = QCheckBox()
        box.setChecked(bool(checked))
        box.toggled.connect(on_change)
        return box

    def _bool(self, key: str, default: bool) -> bool:
        return self.settings.value(key, default, type=bool)

    def _appearance(self, layout) -> None:
        win = self.window_ref
        c = card(layout, "Look")
        self.cb_dark = self._check(getattr(win, "dark_mode", False), self._set_dark)
        setting_row(c, "Dark mode", self.cb_dark)
        self.cb_vertical_tabs = self._check(self._bool("app/vertical_tabs", False), self._set_vertical_tabs)
        setting_row(c, "Vertical tabs", self.cb_vertical_tabs, "Tabs in a column on the left, like a browser's side bar.")
        self.cb_floating_fs = self._check(
            self._bool("app/floating_fs_btn", True), lambda v: self._set_value("app/floating_fs_btn", v)
        )
        setting_row(c, "Floating full-screen button", self.cb_floating_fs)
        self.txt_custom_name = QLineEdit(self.settings.value("homepage/custom_name", "", type=str))
        self.txt_custom_name.setPlaceholderText("Your account name")
        self.txt_custom_name.setMinimumWidth(220)
        self.txt_custom_name.editingFinished.connect(
            lambda: self._set_value("homepage/custom_name", self.txt_custom_name.text().strip())
        )
        setting_row(c, "Name on the start page", self.txt_custom_name)

        c = card(layout, "Reader toolbar", "Where the PDF reader's buttons go. Changes apply to open documents at once.")
        self.toolbar_editor = ToolbarLayoutEditor()
        c.addWidget(self.toolbar_editor)

        c = card(layout, "Display size")
        scale = row_container()
        row = QHBoxLayout(scale)
        row.setContentsMargins(0, 0, 0, 0)
        self.slider_scale = QSlider(Qt.Orientation.Horizontal)
        self.slider_scale.setRange(50, 250)
        self.slider_scale.setMinimumWidth(220)
        self.spin_scale = QSpinBox()
        self.spin_scale.setRange(50, 250)
        self.spin_scale.setSuffix("%")
        current = self.settings.value("app/ui_scale", 100, type=int)
        self.slider_scale.setValue(current)
        self.spin_scale.setValue(current)
        self.slider_scale.valueChanged.connect(self.spin_scale.setValue)
        self.spin_scale.valueChanged.connect(self.slider_scale.setValue)
        self.spin_scale.valueChanged.connect(self._set_scale)
        reset = QPushButton("Reset")
        reset.clicked.connect(lambda: self.slider_scale.setValue(100))
        row.addWidget(self.slider_scale)
        row.addWidget(self.spin_scale)
        row.addWidget(reset)
        setting_row(c, "Interface size", scale)
        self.scale_note = QLabel("Restart Riemann to apply the new size.")
        self.scale_note.setObjectName("restartNote")
        self.scale_note.setVisible(False)
        c.addWidget(self.scale_note)

        c = card(layout, "Window")
        self.cb_window_snap = self._check(
            self._bool("app/window_snapping", snapping_default()), self._set_snapping
        )
        setting_row(
            c,
            "Snap the window to screen edges",
            self.cb_window_snap,
            "Drag the window to an edge to tile it.",
        )
        self.snap_note = QLabel(
            "Takes effect after a restart: on Wayland, snapping runs Riemann through XWayland."
        )
        self.snap_note.setObjectName("restartNote")
        self.snap_note.setWordWrap(True)
        self.snap_note.setVisible(False)
        c.addWidget(self.snap_note)

    def _documents(self, layout) -> None:
        c = card(layout, "Opening documents")
        self.cb_preview_mode = self._check(
            self._bool("reader/open_in_preview_mode", False), self._set_preview_mode
        )
        setting_row(
            c,
            "Open documents in preview mode",
            self.cb_preview_mode,
            "One click previews a file, a double click keeps it.",
        )
        folder = row_container()
        row = QHBoxLayout(folder)
        row.setContentsMargins(0, 0, 0, 0)
        self.txt_default_dir = QLineEdit(self.settings.value("app/default_dir", "", type=str))
        self.txt_default_dir.setPlaceholderText("The last folder used")
        self.txt_default_dir.setMinimumWidth(260)
        self.txt_default_dir.editingFinished.connect(
            lambda: self._set_value("app/default_dir", self.txt_default_dir.text().strip())
        )
        browse = QPushButton("Browse")
        browse.clicked.connect(self._browse_default_dir)
        row.addWidget(self.txt_default_dir, 1)
        row.addWidget(browse)
        setting_row(c, "Folder file dialogs open in", folder)

        c = card(layout, "Reading")
        self.spin_autoscroll = QSpinBox()
        self.spin_autoscroll.setRange(1, 100)
        self.spin_autoscroll.setSuffix(" px/tick")
        self.spin_autoscroll.setValue(self.settings.value("app/autoscroll_speed", 5, type=int))
        self.spin_autoscroll.valueChanged.connect(lambda v: self._set_value("app/autoscroll_speed", v))
        setting_row(c, "Auto-scroll speed", self.spin_autoscroll)

        c = card(layout, "Read aloud")
        self.slider_read_rate = QSlider(Qt.Orientation.Horizontal)
        self.slider_read_rate.setRange(-10, 10)
        self.slider_read_rate.setFixedWidth(220)
        self.slider_read_rate.setValue(round(self.settings.value(READ_RATE_KEY, 0.0, type=float) * 10))
        self.slider_read_rate.valueChanged.connect(lambda v: self._set_value(READ_RATE_KEY, v / 10))
        setting_row(c, "Speed", self.slider_read_rate, "Slower to the left, faster to the right.")
        voice = row_container()
        voice_row = QHBoxLayout(voice)
        voice_row.setContentsMargins(0, 0, 0, 0)
        self.combo_voice = VoicePicker(str(self.settings.value(READ_VOICE_KEY, "") or ""), self._voice_names)
        self.combo_voice.setMinimumWidth(220)
        self.combo_voice.currentIndexChanged.connect(lambda _: self._set_value(READ_VOICE_KEY, self.combo_voice.voice()))
        try_voice = QPushButton("Try it")
        try_voice.clicked.connect(self._try_voice)
        voice_row.addWidget(self.combo_voice, 1)
        voice_row.addWidget(try_voice)
        setting_row(c, "Voice", voice, "Voices come from the system's speech engine.")

        c = card(layout, "Annotations")
        self.cb_store_annotations = self._check(
            self._bool("annotations/store_in_pdf", True),
            lambda v: self._set_value("annotations/store_in_pdf", v),
        )
        setting_row(
            c,
            "Save annotations inside PDF files",
            self.cb_store_annotations,
            "Other PDF apps then show them too. Signed, encrypted and read-only files keep them in Riemann only.",
        )

    def _browser(self, layout) -> None:
        win = self.window_ref
        c = card(layout, "Downloads")
        self.cb_auto_pdf = self._check(
            self._bool("browser/auto_open_pdf", False),
            lambda v: win.toggle_auto_pdf(v) if hasattr(win, "toggle_auto_pdf") else self._set_value("browser/auto_open_pdf", v),
        )
        setting_row(c, "Open downloaded PDFs in Riemann", self.cb_auto_pdf)

        c = card(
            layout,
            "Address bar suggestions",
            "Sites suggested as you type, besides your history. Double-click one to edit it.",
        )
        self.sites_editor = StringListEditor(
            string_list(POPULAR_SITES_KEY, DEFAULT_POPULAR_SITES), "example.com"
        )
        self.sites_editor.changed.connect(self._set_popular_sites)
        c.addWidget(self.sites_editor)

    def _permissions(self, layout) -> None:
        self.permissions_editor = PermissionsEditor(permissions.shared())
        layout.addWidget(self.permissions_editor)

    def _ai(self, layout) -> None:
        c = card(
            layout,
            "Subject tags",
            "Labels the AI engine may give a paper when it reads its first page. Leave empty for general subjects.",
        )
        self.tags_editor = StringListEditor(string_list(AI_TAGS_KEY), "Number theory")
        self.tags_editor.changed.connect(lambda values: set_string_list(AI_TAGS_KEY, values))
        c.addWidget(self.tags_editor)

        c = card(layout, "OCR languages", "Languages Tesseract reads when you OCR a page.")
        from ..reader.workers import available_ocr_languages

        chosen = set(str(self.settings.value(OCR_LANGUAGES_KEY, "eng") or "eng").split("+"))
        available = available_ocr_languages()
        self.ocr_boxes: dict[str, QCheckBox] = {}
        if not available:
            missing = QLabel("Tesseract was not found. Install it to use OCR.")
            missing.setObjectName("hint")
            c.addWidget(missing)
        grid = QGridLayout()
        for i, lang in enumerate(available):
            box = QCheckBox(lang)
            box.setChecked(lang in chosen)
            box.toggled.connect(self._set_ocr_languages)
            self.ocr_boxes[lang] = box
            grid.addWidget(box, i // 4, i % 4)
        c.addLayout(grid)

    def _privacy(self, layout) -> None:
        c = card(layout, "Browsing data")
        grid = QGridLayout()
        actions = [
            ("Clear browsing history", self.clear_history),
            ("Clear download list", self.clear_downloads),
            ("Clear cookies", self.clear_cookies),
            ("Clear cookies for one site", self.manage_cookies),
            ("Clear cache", self.clear_cache),
            ("Clear all of the above", self.clear_all),
        ]
        self.data_buttons = {}
        for i, (label, slot) in enumerate(actions):
            btn = QPushButton(label)
            btn.clicked.connect(slot)
            if slot == self.clear_all:
                btn.setProperty("role", "danger")
            self.data_buttons[label] = btn
            grid.addWidget(btn, i // 2, i % 2)
        c.addLayout(grid)
        c = card(layout, "Optional components", "Extra Python packages for snipping maths to LaTeX, captions and video downloads.")
        deps = QPushButton("Manage optional components")
        deps.clicked.connect(lambda: DependenciesDialog(self).exec())
        c.addWidget(deps, 0, Qt.AlignmentFlag.AlignLeft)

    def _about(self, layout) -> None:
        c = card(layout, "Riemann")
        version = app_version()
        c.addWidget(QLabel(f"Version {version}" if version else "Development build"))
        log_row = row_container()
        row = QHBoxLayout(log_row)
        row.setContentsMargins(0, 0, 0, 0)
        folder = log_folder()
        path = QLabel(folder)
        path.setObjectName("hint")
        path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        open_btn = QPushButton("Open log folder")
        open_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(folder)))
        row.addWidget(path, 1)
        row.addWidget(open_btn)
        setting_row(c, "Log", log_row)
        note = QLabel(
            "The interface font is Inter, under the SIL Open Font License (assets/fonts/Inter-OFL.txt). "
            "Icons are from Lucide."
        )
        note.setObjectName("hint")
        note.setWordWrap(True)
        c.addWidget(note)

    def _set_value(self, key: str, value) -> None:
        self.settings.setValue(key, value)

    def _speech(self):
        if getattr(self, "_tts", None) is None:
            self._tts = make_speech()
        return self._tts

    def _voice_names(self) -> list:
        tts = self._speech()
        return [v.name() for v in tts.availableVoices()] if tts is not None else []

    def _try_voice(self) -> None:
        tts = self._speech()
        if tts is None:
            QMessageBox.information(self, "Read aloud", NO_ENGINE)
            return
        name = self.combo_voice.voice()
        for v in tts.availableVoices():
            if v.name() == name:
                tts.setVoice(v)
                break
        tts.setRate(self.slider_read_rate.value() / 10)
        tts.stop()
        tts.say("This is how Riemann reads your documents aloud.")

    def release(self) -> None:
        tts = getattr(self, "_tts", None)
        if tts is not None:
            tts.stop()
        super().release()

    def _set_dark(self, checked: bool) -> None:
        win = self.window_ref
        if hasattr(win, "toggle_ui_theme") and bool(checked) != bool(getattr(win, "dark_mode", False)):
            win.toggle_ui_theme()

    def _set_vertical_tabs(self, checked: bool) -> None:
        win = self.window_ref
        if hasattr(win, "set_vertical_tabs"):
            win.set_vertical_tabs(bool(checked))
        else:
            self._set_value("app/vertical_tabs", bool(checked))

    def _set_scale(self, value: int) -> None:
        self._set_value("app/ui_scale", int(value))
        self.scale_note.setVisible(True)

    def _set_snapping(self, checked: bool) -> None:
        self._set_value("app/window_snapping", bool(checked))
        from ...app import RiemannWindow

        for win in list(RiemannWindow._all_open_windows) + [self.window_ref]:
            snapper = getattr(win, "window_snapper", None)
            if snapper is not None:
                snapper.set_enabled(bool(checked))
        self.snap_note.setVisible(is_wayland_session())

    def _set_preview_mode(self, checked: bool) -> None:
        win = self.window_ref
        old = self._bool("reader/open_in_preview_mode", False)
        self._set_value("reader/open_in_preview_mode", bool(checked))
        if old == bool(checked) or not hasattr(win, "_switch_open_documents_mode"):
            return
        if not self._has_open_documents():
            return
        mode = "preview mode" if checked else "full mode"
        answer = QMessageBox.question(
            self,
            "Open documents",
            f"Switch the documents that are already open to {mode} too?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            win._switch_open_documents_mode(bool(checked))

    def _has_open_documents(self) -> bool:
        win = self.window_ref
        for tabs in (getattr(win, "tabs_main", None), getattr(win, "tabs_side", None)):
            if tabs is None:
                continue
            for i in range(tabs.count()):
                w = tabs.widget(i)
                if getattr(w, "current_path", None) and w is not getattr(win, "preview_tab_widget", None):
                    return True
        return False

    def _browse_default_dir(self) -> None:
        start = self.txt_default_dir.text() or QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.DocumentsLocation
        )
        folder = QFileDialog.getExistingDirectory(self, "Folder for file dialogs", start)
        if folder:
            self.txt_default_dir.setText(folder)
            self._set_value("app/default_dir", folder)

    def _set_popular_sites(self, values: list) -> None:
        set_string_list(POPULAR_SITES_KEY, values)
        manager = getattr(self.window_ref, "history_manager", None)
        if manager is not None:
            manager.popular_sites = string_list(POPULAR_SITES_KEY, DEFAULT_POPULAR_SITES)
            model = getattr(self.window_ref, "history_model", None)
            if model is not None:
                model.setStringList(manager.get_model_data())

    def _set_ocr_languages(self) -> None:
        chosen = [lang for lang, box in self.ocr_boxes.items() if box.isChecked()]
        self._set_value(OCR_LANGUAGES_KEY, "+".join(chosen) or "eng")

    def _done(self, message: str) -> None:
        show = getattr(self.window_ref, "show_toast", None)
        if show:
            show(message)

    def clear_history(self) -> None:
        win = self.window_ref
        win.history_manager.history["web"] = []
        win.history_manager.save()
        win.history_model.setStringList(win.history_manager.get_model_data())
        self._done("Browsing history cleared.")

    def clear_downloads(self) -> None:
        dl = self.window_ref.download_manager_dialog
        dl.table.setRowCount(0)
        dl.downloads.clear()
        dl._persist_entries()
        self._done("Download list cleared.")

    def clear_cookies(self) -> None:
        if not self.window_ref.incognito:
            self.window_ref.web_profile.cookieStore().deleteAllCookies()
        self._done("Cookies cleared.")

    def manage_cookies(self) -> None:
        from ...window.dialogs import ManageCookiesDialog

        ManageCookiesDialog(self.window_ref).exec()

    def clear_cache(self) -> None:
        if not self.window_ref.incognito:
            self.window_ref.web_profile.clearHttpCache()
        self._done("Cache cleared.")

    def clear_all(self) -> None:
        answer = QMessageBox.question(
            self,
            "Clear browsing data",
            "Clear browsing history, the download list, cookies and the cache?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.clear_history()
        self.clear_downloads()
        self.clear_cookies()
        self.clear_cache()
        self._done("Browsing data cleared.")

