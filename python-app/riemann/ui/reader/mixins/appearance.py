"""
Page themes, the start page shown before a document is open, full screen and toasts.

Part of ReaderTab (riemann/ui/reader/tab.py). ReaderTab lists this mixin before
QWidget, so its Qt event handlers override QWidget's.

Notes:

_setup_home_page: Set by the per-tab theme toggle (N); None follows the
interface theme. Clicking the empty page takes focus from the path field, so N
(theme toggle) is not typed into it. The two columns need about 710 px. Without
an explicit minimum that becomes every reader tab's minimum width, and with it
the window's, which stops the desktop from tiling the window to half the
screen.
"""

import os
import sys
from typing import Optional
from PySide6.QtCore import (
    QSize,
    Qt,
    QTimer,
)
from PySide6.QtGui import (
    QColor,
    QPalette,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from ..widgets import DropZoneLabel
from ...palette import (
    FONT_STACK,
    GridCanvas,
    link_tip_style,
    path_row,
    short_folder,
    tinted_pixmap,
    toast_style,
    tokens,
)
from ..notes import notes_path_for as _notes_for
from ....core.settings import app_settings
from .host import ReaderHostBase


class AppearanceMixin(ReaderHostBase):
    _reader_fullscreen = False
    lbl_toast = None

    def apply_theme(self) -> None:
        is_dark = self.theme_mode != 0
        t = tokens(is_dark)
        if self.signature_banner is not None:
            self._style_signature_banner(self._signature_banner_kind)
        pal = self.palette()
        color = QColor(t["surface"])
        pal.setColor(QPalette.ColorRole.Window, color)
        self.setPalette(pal)

        if self.web:
            self.web.page().setBackgroundColor(color)

        self.scroll_content.setStyleSheet(
            f"#scrollContent {{ background-color: {t['canvas']}; }}"
        )

        fg = t["text"]
        checked_bg = t["accent_soft"]
        checked_border = t["accent"]
        hover_bg = t["raised"]

        sb_bg = t["raised"]
        sb_fg = t["text"]
        input_bg = t["canvas"]
        input_border = t["border_strong"]

        if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
            base_p = getattr(sys, "_MEIPASS")
            arrow_path = os.path.join(
                base_p,
                "riemann",
                "assets",
                "icons",
                "chevron-down-white.svg" if is_dark else "chevron-down.svg",
            )
        else:
            base_p = os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            )
            arrow_path = os.path.join(
                base_p,
                "assets",
                "icons",
                "chevron-down-white.svg" if is_dark else "chevron-down.svg",
            )

        arrow_url = arrow_path.replace("\\", "/")

        self.toolbar.setStyleSheet(f"""
            QWidget {{ background: {color.name()}; color: {fg}; }}
            QPushButton, QToolButton {{ border: 1px solid transparent; padding: 6px; border-radius: 4px; background: transparent; }}
            QPushButton:hover, QToolButton:hover {{ background: {hover_bg}; }}
            QPushButton:checked, QToolButton:checked {{ background-color: {checked_bg}; border: 1px solid {checked_border}; }}
            QToolButton#groupMenu {{ padding: 6px 24px 6px 10px; }}
            QToolButton#groupMenu::menu-indicator {{ image: url("{arrow_url}"); width: 12px; height: 12px;
                subcontrol-origin: padding; subcontrol-position: right center; right: 6px; }}
            QToolButton#moreButton {{ font-size: 18px; font-weight: 600; padding: 2px 10px; }}
            QToolButton#moreButton::menu-indicator {{ image: none; width: 0px; }}
            QFrame#toolbarSeparator {{ background: {input_border}; }}
            QFrame#toolbarRail {{ border-left: 1px solid {input_border}; }}
            QFrame#toolbarPill {{ border: 1px solid {input_border}; border-radius: 22px; }}
            QWidget#toolbarRibbon {{ border-top: 1px solid {input_border}; }}
            QComboBox {{ background-color: {input_bg}; color: {sb_fg}; border: 1px solid {input_border}; border-radius: 4px; padding: 4px; }}
            QComboBox::drop-down {{ border: none; width: 24px; }}
            QComboBox::down-arrow {{ image: url("{arrow_url}"); width: 16px; height: 16px; }}
        """)
        if self.toolbar_layout is not None:
            self.toolbar_layout.restyle()

        self.search_bar.setStyleSheet(f"""
            QWidget {{ background-color: {sb_bg};
                color: {sb_fg}; }}
            QLineEdit {{ background-color: {input_bg}; 
                color: {sb_fg}; 
                border: 1px solid {input_border}; 
                border-radius: 4px; 
                padding: 4px; }}
            QPushButton {{ background: transparent; 
                border: none; }}
            QPushButton:hover {{ background: {t['border']}; 
                border-radius: 4px; }}
        """)

        self.link_tooltip.setStyleSheet(link_tip_style(t))
        self.ai_search_bar.setStyleSheet(
            f"QWidget {{ background-color: {sb_bg}; color: {sb_fg}; }}"
            f"QLineEdit {{ background-color: {input_bg}; color: {sb_fg}; border: 1px solid {t['accent']}; "
            "border-radius: 4px; padding: 4px; }"
            "QPushButton { background: transparent; border: none; }"
            f"QPushButton:hover {{ background: {t['border']}; border-radius: 4px; }}"
            f"QPushButton#askAi {{ background-color: {t['accent']}; color: {t['accent_text']}; "
            "border-radius: 4px; padding: 4px 10px; }"
            f"QPushButton#askAi:hover {{ background-color: {t['accent_hover']}; }}"
        )

        self._update_icons()

    def toggle_theme(self) -> None:
        """
        Cycles configuration properties through Light, Fast Dark, and Smart Dark modes.
        Saves preferences globally and triggers visual reconstructions safely.
        On the start page (no document open) it switches the page between light and dark instead.
        """
        if self._showing_home_page():
            self._home_dark_override = not self._home_dark
            self._style_home_page(self._home_dark_override)
            self.show_toast(
                "Home page: Dark Mode" if self._home_dark_override else "Home page: Light Mode"
            )
            return

        self.theme_mode = (self.theme_mode + 1) % 3
        self.settings.setValue("themeMode", self.theme_mode)
        self.apply_theme()
        self.sidebar_nav.set_theme_mode(self.theme_mode)

        if self._rebuild_debounce_timer is None:
            self._rebuild_debounce_timer = QTimer(self)
            self._rebuild_debounce_timer.setSingleShot(True)
            self._rebuild_debounce_timer.setInterval(200)
            self._rebuild_debounce_timer.timeout.connect(self._do_rebuild_and_render)
        self._rebuild_debounce_timer.start()

        mode_names = ["Light Mode", "Fast Dark Mode", "Smart Dark Mode"]
        self.show_toast(f"Theme set to: {mode_names[self.theme_mode]}")

    def _populate_home_recents(self) -> None:
        """Fills the start page's recent documents list from the history manager."""
        win = self.window()
        dark = self._home_dark_override
        if dark is None:
            dark = getattr(win, "dark_mode", self._home_dark)
        if dark != self._home_dark:
            self._style_home_page(dark)
            return
        self.list_recent.clear()
        t = tokens(self._home_dark)
        icon_pm = tinted_pixmap("file-text", t["muted"], 18)

        if win and hasattr(win, "history_manager"):
            recent_pdfs = win.history_manager.get_list("pdf")
            for path in recent_pdfs[:13]:
                if not os.path.exists(path):
                    continue
                folder = short_folder(os.path.dirname(path))
                item = QListWidgetItem()
                item.setToolTip(path)
                item.setData(Qt.ItemDataRole.UserRole, path)
                item.setSizeHint(QSize(0, 54))
                self.list_recent.addItem(item)
                self.list_recent.setItemWidget(
                    item, path_row(icon_pm, os.path.basename(path), folder)
                )

        count = self.list_recent.count()
        self.list_recent.setFixedHeight(min(count, 9) * 56 + 4)
        self.lbl_recent_count.setText(str(count) if count else "")
        self.list_recent.setVisible(count > 0)
        self.lbl_recent_empty.setVisible(count == 0)

    def _setup_home_page(self) -> None:
        """Builds the start page shown in an empty reader tab: a drop target, a path
        field, a browse button and the recent documents list."""
        self._home_dark = app_settings().value(
            "darkMode", False, type=bool
        )
        self._home_dark_override: Optional[bool] = None
        self.home_page_widget = GridCanvas(self._home_dark)
        self.home_page_widget.setObjectName("pdfHome")
        self.home_page_widget.setFocusPolicy(Qt.FocusPolicy.ClickFocus)

        outer = QHBoxLayout(self.home_page_widget)
        outer.setContentsMargins(40, 36, 40, 36)
        outer.setSpacing(40)

        left = QVBoxLayout()
        left.setSpacing(0)

        title = QLabel("Riemann")
        title.setObjectName("homeWordmark")
        subtitle = QLabel("High-Performance Research Environment")
        subtitle.setObjectName("homeTagline")

        self.drop_zone = DropZoneLabel("")
        self.drop_zone.setObjectName("dropZone")
        self.drop_zone.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.drop_zone.setMinimumHeight(190)
        self.drop_zone.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.drop_zone.file_dropped.connect(self._on_file_dropped)
        dz = QVBoxLayout(self.drop_zone)
        dz.setContentsMargins(16, 24, 16, 24)
        dz.setSpacing(6)
        self.lbl_drop_icon = QLabel()
        drop_title = QLabel("Drop a PDF here")
        drop_title.setObjectName("dropTitle")
        drop_sub = QLabel("Markdown files work too")
        drop_sub.setObjectName("dropSub")
        dz.addStretch()
        for w in (self.lbl_drop_icon, drop_title, drop_sub):
            w.setAlignment(Qt.AlignmentFlag.AlignCenter)
            w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            dz.addWidget(w)
        dz.insertSpacing(2, 6)
        dz.addStretch()

        self.txt_open_path = QLineEdit()
        self.txt_open_path.setObjectName("homePath")
        self.txt_open_path.setPlaceholderText("Paste a file path and press Enter")
        self.txt_open_path.returnPressed.connect(self._on_home_path_entered)

        self.btn_home_browse = QPushButton("Browse Files")
        self.btn_home_browse.setObjectName("homePrimary")
        self.btn_home_browse.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_home_browse.clicked.connect(self.open_pdf_dialog)
        browse_hint = QLabel("Ctrl+O")
        browse_hint.setObjectName("homeHint")

        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)
        btn_row.addWidget(self.btn_home_browse)
        btn_row.addWidget(browse_hint)
        btn_row.addStretch()

        left.addStretch(2)
        left.addWidget(title)
        left.addSpacing(4)
        left.addWidget(subtitle)
        left.addSpacing(32)
        left.addWidget(self.drop_zone)
        left.addSpacing(16)
        left.addWidget(self.txt_open_path)
        left.addSpacing(16)
        left.addLayout(btn_row)
        left.addStretch(3)

        recent_card = QFrame()
        recent_card.setObjectName("homeCard")
        right = QVBoxLayout(recent_card)
        right.setContentsMargins(8, 18, 8, 8)
        right.setSpacing(6)

        header = QHBoxLayout()
        header.setContentsMargins(14, 0, 14, 0)
        recent_label = QLabel("Recent Documents")
        recent_label.setObjectName("homeSection")
        self.lbl_recent_count = QLabel("")
        self.lbl_recent_count.setObjectName("homeHint")
        header.addWidget(recent_label)
        header.addStretch()
        header.addWidget(self.lbl_recent_count)

        self.list_recent = QListWidget()
        self.list_recent.setObjectName("homeRecent")
        self.list_recent.setCursor(Qt.CursorShape.PointingHandCursor)
        self.list_recent.setMinimumWidth(260)
        self.list_recent.setVerticalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerPixel
        )
        self.list_recent.itemClicked.connect(self._on_recent_item_clicked)

        self.lbl_recent_empty = QLabel("Documents you open will show up here.")
        self.lbl_recent_empty.setObjectName("homeEmpty")
        self.lbl_recent_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_recent_empty.hide()

        right.addLayout(header)
        right.addSpacing(6)
        right.addWidget(self.list_recent)
        right.addWidget(self.lbl_recent_empty)

        left_box = QWidget()
        left_box.setLayout(left)
        left_box.setMaximumWidth(520)
        recent_card.setMaximumWidth(620)

        right_col = QVBoxLayout()
        right_col.addStretch(2)
        right_col.addWidget(recent_card)
        right_col.addStretch(3)

        outer.addStretch(1)
        outer.addWidget(left_box, 5)
        outer.addLayout(right_col, 6)
        outer.addStretch(1)
        self.home_page_widget.setMinimumSize(1, 1)

        self._style_home_page(self._home_dark)

    def _style_home_page(self, dark: bool) -> None:
        """Applies the light or dark palette to the start page."""
        self._home_dark = dark
        t = tokens(dark)
        self.home_page_widget.set_dark(dark)
        self.home_page_widget.setStyleSheet(f"""
            QWidget {{ background: transparent; color: {t["text"]}; font-family: {FONT_STACK}; }}
            #homeWordmark {{ font-size: 44px; font-weight: 300; letter-spacing: 3px; color: {t["text"]}; }}
            #homeTagline {{ font-size: 15px; color: {t["muted"]}; }}
            #homeSection {{ font-size: 15px; font-weight: 600; color: {t["text"]}; }}
            #homeHint {{ font-size: 13px; color: {t["faint"]}; }}
            #homeEmpty {{ font-size: 14px; color: {t["faint"]}; padding: 40px 12px; }}
            #homeCard {{
                background: {t["surface"]}; border: 1px solid {t["border"]}; border-radius: 16px;
            }}
            #dropZone {{
                background: {t["surface"]}; border: 2px dashed {t["border_strong"]};
                border-radius: 16px; color: {t["muted"]}; font-size: 15px;
            }}
            #dropZone:hover {{ border-color: {t["faint"]}; }}
            #dropZone[dragActive="true"] {{ background: {t["accent_soft"]}; border-color: {t["accent"]}; }}
            #dropTitle {{ font-size: 17px; font-weight: 600; color: {t["text"]}; }}
            #dropZone[dragActive="true"] #dropTitle {{ color: {t["accent"]}; }}
            #dropSub {{ font-size: 13px; color: {t["muted"]}; }}
            QLineEdit#homePath {{
                background: {t["surface"]}; border: 1px solid {t["border"]}; border-radius: 10px;
                padding: 10px 14px; font-size: 14px; color: {t["text"]};
                selection-background-color: {t["accent"]}; selection-color: {t["accent_text"]};
            }}
            QLineEdit#homePath:focus {{ border-color: {t["accent"]}; }}
            QPushButton#homePrimary {{
                background: {t["accent"]}; color: {t["accent_text"]}; border: none; border-radius: 10px;
                padding: 10px 22px; font-size: 14px; font-weight: 600;
            }}
            QPushButton#homePrimary:hover {{ background: {t["accent_hover"]}; }}
            QListWidget#homeRecent {{ background: transparent; border: none; outline: none; padding: 0; }}
            QListWidget#homeRecent::item {{ border: none; border-radius: 10px; margin: 1px 0; padding: 0; }}
            QListWidget#homeRecent::item:hover {{ background: {t["raised"]}; }}
            QListWidget#homeRecent::item:selected {{ background: {t["accent_soft"]}; }}
            #recentName {{ font-size: 14px; color: {t["text"]}; }}
            #recentDir {{ font-size: 12px; color: {t["faint"]}; }}
            QScrollBar:vertical {{ background: transparent; width: 14px; }}
            QScrollBar::handle:vertical {{ background: {t["border_strong"]}; border-radius: 5px; margin: 2px; min-height: 24px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        """)
        self.lbl_drop_icon.setPixmap(tinted_pixmap("file-text", t["accent"], 36))
        if self.list_recent.count():
            self._populate_home_recents()

    def apply_ui_theme(self, dark: bool) -> None:
        """Called by the main window when the interface theme changes."""
        self.sidebar_nav.update_theme(dark)
        self.notes_panel.apply_theme(dark)
        self.citation_card.apply_theme(dark)
        self._home_dark_override = None
        if dark != self._home_dark:
            self._style_home_page(dark)

    def _showing_home_page(self) -> bool:
        stack = self.stack
        return (
            stack is not None
            and stack.currentWidget() is self.home_page_widget
        )

    def _on_home_path_entered(self) -> None:
        path = self.txt_open_path.text().strip().strip('"').strip("'")
        if os.path.exists(path) and os.path.isfile(path):
            self.load_document(path)
        else:
            self.show_toast("File not found on disk.")

    def _on_recent_item_clicked(self, item) -> None:
        path = item.data(Qt.ItemDataRole.UserRole)
        if os.path.exists(path):
            self.load_document(path)
        else:
            self.show_toast("File no longer exists at this location.")

    def toggle_reader_fullscreen(self) -> None:
        from ...app import RiemannWindow

        if self.window() and isinstance(self.window(), RiemannWindow):
            self.window().toggle_reader_fullscreen()

    def update_fullscreen_icon(self, state: int) -> None:
        icons = {0: "panel-top.svg", 1: "maximize.svg", 2: "minimize.svg"}
        icon_name = icons.get(state, "maximize.svg")
        self.btn_fullscreen.setIcon(self._get_icon(icon_name))
        if self.toolbar_layout is not None:
            self.toolbar_layout.set_fullscreen(state == 2)

    def show_toast(self, msg: str) -> None:
        self.lbl_toast = QLabel(self)
        self.lbl_toast.setStyleSheet(toast_style(tokens(self.theme_mode != 0)))
        self.lbl_toast.setText(msg)
        self.lbl_toast.adjustSize()
        self.lbl_toast.move(
            (self.width() - self.lbl_toast.width()) // 2, self.height() - 80
        )
        self.lbl_toast.show()
        QTimer.singleShot(4000, self.lbl_toast.hide)

    def start_presentation(self) -> None:
        """Shows the document full screen, one page at a time, from the current page."""
        if not self.current_doc:
            return
        from ..presentation import PresentationWindow

        self._presentation = PresentationWindow(self.current_doc, self.current_page_index)
        self._presentation.destroyed.connect(lambda *_: setattr(self, "_presentation", None))
        self._presentation.start()

    def toggle_notes(self, *_) -> None:
        """Shows or hides the notes panel for the open document."""
        panel = self.notes_panel
        show = not panel.isVisible()
        if show:
            if self.current_path and panel.path != _notes_for(self.current_path):
                panel.load(self.current_path)
            panel.show()
            panel.editor.setFocus()
        else:
            panel.save()
            panel.hide()
        self.btn_notes.setChecked(show)
        self.settings.setValue("reader/notes_visible", show)

    def _open_link_in_browser(self, url: str) -> None:
        window = self.window()
        if hasattr(window, "new_browser_tab"):
            window.new_browser_tab(url)
