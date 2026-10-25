"""
Screen-edge snapping for top-level windows.

Dragging a window by its title bar until the pointer touches a screen edge
tiles it, with a translucent preview of the target area shown first:

    left / right edge              -> left / right half
    bottom edge                    -> bottom half
    top edge, middle part          -> full screen (maximised)
    top edge, near either corner   -> top half

Dragging a snapped window away from the edge restores its previous size.

Window managers move windows themselves, so a drag is recognised from the
stream of move events: while the title bar is held, the pointer keeps the same
offset from the window frame. The drag ends when the left button is released
(queried from the platform where possible) or the window stops moving.

Native Wayland compositors do not tell applications where their windows are and
do not let them place windows, so snapping only works on X11 (including
XWayland), Windows and macOS. On a Wayland session it is therefore off by
default; turning it on makes Riemann run through XWayland (see
`prefer_xwayland_for_snapping`), which can look soft under fractional scaling.
"""

import ctypes
import ctypes.util
import os
import sys
import time
from typing import Optional

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QWidget

EDGE_PX = 8
CORNER_FRACTION = 0.22


def is_wayland_session() -> bool:
    return sys.platform.startswith("linux") and (
        os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
        or bool(os.environ.get("WAYLAND_DISPLAY"))
    )


def snapping_default() -> bool:
    """Snapping is on by default wherever it works without switching platforms."""
    return not is_wayland_session()


def prefer_xwayland_for_snapping() -> bool:
    """
    On a Wayland session, asks Qt to use XWayland (the xcb platform) so the
    window can be positioned. Must run before QApplication is created. Does
    nothing if the user already chose a platform or XWayland is unavailable.

    Returns:
        bool: True if the platform was switched to xcb.
    """
    if not sys.platform.startswith("linux"):
        return False
    if os.environ.get("QT_QPA_PLATFORM"):
        return False
    if not is_wayland_session() or not os.environ.get("DISPLAY"):
        return False
    try:
        from PySide6.QtCore import QLibraryInfo

        plugins = QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath)
        if not os.path.exists(os.path.join(plugins, "platforms", "libqxcb.so")):
            return False
    except Exception:
        return False
    os.environ["QT_QPA_PLATFORM"] = "xcb"
    return True


class _ButtonState:
    """Reads whether the primary mouse button is held, straight from the OS."""

    def __init__(self) -> None:
        self._x11 = None
        self._x11_display = None
        self._win_user32 = None
        # Windows reports the button truthfully during its move loop; X servers
        # (XWayland especially) can report a stale state while the window
        # manager holds the pointer, so there an idle timeout ends the drag.
        self.reliable = False

        if sys.platform == "win32":
            try:
                self._win_user32 = ctypes.windll.user32  # type: ignore[attr-defined]
                self.reliable = True
            except Exception:
                self._win_user32 = None
        elif QGuiApplication.platformName() == "xcb":
            try:
                lib = ctypes.util.find_library("X11")
                if lib:
                    x11 = ctypes.cdll.LoadLibrary(lib)
                    x11.XOpenDisplay.restype = ctypes.c_void_p
                    x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
                    x11.XDefaultRootWindow.restype = ctypes.c_ulong
                    x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
                    x11.XQueryPointer.restype = ctypes.c_int
                    x11.XQueryPointer.argtypes = [
                        ctypes.c_void_p,
                        ctypes.c_ulong,
                        ctypes.POINTER(ctypes.c_ulong),
                        ctypes.POINTER(ctypes.c_ulong),
                        ctypes.POINTER(ctypes.c_int),
                        ctypes.POINTER(ctypes.c_int),
                        ctypes.POINTER(ctypes.c_int),
                        ctypes.POINTER(ctypes.c_int),
                        ctypes.POINTER(ctypes.c_uint),
                    ]
                    display = x11.XOpenDisplay(None)
                    if display:
                        self._x11 = x11
                        self._x11_display = display
            except Exception:
                self._x11 = None

    def left_down(self) -> Optional[bool]:
        """True/False when the platform can tell, None when it cannot."""
        if self._win_user32 is not None:
            try:
                vk = 0x02 if self._win_user32.GetSystemMetrics(23) else 0x01
                return bool(self._win_user32.GetAsyncKeyState(vk) & 0x8000)
            except Exception:
                return None
        if self._x11 is not None:
            try:
                root = self._x11.XDefaultRootWindow(self._x11_display)
                root_ret, child_ret = ctypes.c_ulong(), ctypes.c_ulong()
                rx, ry, wx, wy = (ctypes.c_int() for _ in range(4))
                mask = ctypes.c_uint()
                self._x11.XQueryPointer(
                    self._x11_display,
                    root,
                    ctypes.byref(root_ret),
                    ctypes.byref(child_ret),
                    ctypes.byref(rx),
                    ctypes.byref(ry),
                    ctypes.byref(wx),
                    ctypes.byref(wy),
                    ctypes.byref(mask),
                )
                return bool(mask.value & (1 << 8))
            except Exception:
                return None
        return None


class SnapPreview(QWidget):
    """A click-through translucent rectangle showing where the window will go."""

    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.WindowTransparentForInput,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(6, 6, -6, -6)
        painter.setBrush(QColor(0, 127, 212, 60))
        painter.setPen(QPen(QColor(0, 127, 212, 200), 2))
        painter.drawRoundedRect(rect, 10, 10)
        painter.end()


class WindowSnapper(QObject):
    """Adds drag-to-edge tiling to a top-level window."""

    def __init__(self, window: QWidget, enabled: bool = True) -> None:
        super().__init__(window)
        self.window = window
        self.enabled = enabled
        self._buttons: Optional[_ButtonState] = None
        self._preview: Optional[SnapPreview] = None

        self._last_offset: Optional[QPoint] = None
        self._last_pos: Optional[QPoint] = None
        self._last_move_time = 0.0
        self._dragging = False
        self._zone: Optional[str] = None
        self._suppress_until = 0.0
        self._restore_size = None  # size to return to when a snapped window is dragged off

        self._poll = QTimer(self)
        self._poll.setInterval(40)
        self._poll.timeout.connect(self._on_poll)

        window.installEventFilter(self)

    @staticmethod
    def platform_supported() -> bool:
        return not QGuiApplication.platformName().startswith("wayland")

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled
        if not enabled:
            self._end_drag(apply=False)

    # ---- Drag tracking ---------------------------------------------------

    def eventFilter(self, obj, event) -> bool:
        if (
            obj is self.window
            and event.type() == QEvent.Type.Move
            and self.enabled
            and self.platform_supported()
        ):
            self._on_window_moved()
        return False

    def _left_down(self) -> Optional[bool]:
        if self._buttons is None:
            self._buttons = _ButtonState()
        return self._buttons.left_down()

    def _on_window_moved(self) -> None:
        now = time.monotonic()
        if now < self._suppress_until:
            return
        win = self.window
        if win.isFullScreen():
            return

        cursor = QCursor.pos()
        frame_pos = win.frameGeometry().topLeft()
        offset = cursor - frame_pos

        if self._last_move_time and now - self._last_move_time > 0.6:
            self._last_offset = None
        moved = self._last_pos is not None and frame_pos != self._last_pos
        follows_pointer = (
            self._last_offset is not None
            and (offset - self._last_offset).manhattanLength() <= 4
        )
        self._last_offset = offset
        self._last_pos = frame_pos
        self._last_move_time = now

        # Only a move that carries the pointer along is a title-bar drag; that
        # excludes restores, maximise/unmaximise and window-manager tiling.
        button = self._left_down()
        if not moved or button is False or not follows_pointer:
            return

        if not self._dragging:
            self._dragging = True
            self._unsnap_for_drag(cursor)
        self._update_zone(cursor)
        if not self._poll.isActive():
            self._poll.start()

    def _on_poll(self) -> None:
        if not self._dragging:
            self._poll.stop()
            return
        self._update_zone(QCursor.pos())
        idle = time.monotonic() - self._last_move_time
        button = self._left_down()
        if button is False:
            self._end_drag(apply=True)
        elif self._buttons is not None and self._buttons.reliable:
            if idle > 10.0:
                self._end_drag(apply=False)
        elif (button is None and idle > 0.35) or idle > 0.9:
            self._end_drag(apply=True)

    def _end_drag(self, apply: bool) -> None:
        zone = self._zone
        self._dragging = False
        self._zone = None
        self._last_offset = None
        self._poll.stop()
        if self._preview is not None:
            self._preview.hide()
        if apply and zone:
            self._apply(zone)

    # ---- Zones -------------------------------------------------------------

    def _screen_at(self, point: QPoint):
        return QGuiApplication.screenAt(point) or self.window.screen()

    def _zone_at(self, point: QPoint) -> Optional[str]:
        screen = self._screen_at(point)
        if screen is None:
            return None
        g = screen.geometry()
        near_left = point.x() <= g.left() + EDGE_PX
        near_right = point.x() >= g.right() - EDGE_PX
        near_top = point.y() <= g.top() + EDGE_PX
        near_bottom = point.y() >= g.bottom() - EDGE_PX

        if near_top:
            corner = g.width() * CORNER_FRACTION
            if point.x() <= g.left() + corner or point.x() >= g.right() - corner:
                return "top"
            return "maximize"
        if near_left:
            return "left"
        if near_right:
            return "right"
        if near_bottom:
            return "bottom"
        return None

    def _target_rect(self, zone: str, point: QPoint) -> Optional[QRect]:
        screen = self._screen_at(point)
        if screen is None:
            return None
        a = screen.availableGeometry()
        half_w = a.width() // 2
        half_h = a.height() // 2
        if zone == "left":
            return QRect(a.left(), a.top(), half_w, a.height())
        if zone == "right":
            return QRect(a.left() + half_w, a.top(), a.width() - half_w, a.height())
        if zone == "top":
            return QRect(a.left(), a.top(), a.width(), half_h)
        if zone == "bottom":
            return QRect(a.left(), a.top() + half_h, a.width(), a.height() - half_h)
        if zone == "maximize":
            return QRect(a)
        return None

    def _update_zone(self, cursor: QPoint) -> None:
        zone = self._zone_at(cursor)
        if zone == self._zone:
            return
        self._zone = zone
        if zone is None:
            if self._preview is not None:
                self._preview.hide()
            return
        rect = self._target_rect(zone, cursor)
        if rect is None:
            return
        if self._preview is None:
            self._preview = SnapPreview()
        self._preview.setGeometry(rect)
        self._preview.show()
        self._preview.raise_()

    # ---- Applying ----------------------------------------------------------

    def _apply(self, zone: str) -> None:
        win = self.window
        cursor = QCursor.pos()
        rect = self._target_rect(zone, cursor)
        if rect is None:
            return
        if self._restore_size is None:
            self._restore_size = win.size()
        self._suppress_until = time.monotonic() + 0.5

        if zone == "maximize":
            win.showMaximized()
            return
        if win.isMaximized():
            win.showNormal()

        # rect is where the whole frame should go; setGeometry places the client area.
        frame, inner = win.frameGeometry(), win.geometry()
        left = inner.left() - frame.left()
        top = inner.top() - frame.top()
        right = frame.right() - inner.right()
        bottom = frame.bottom() - inner.bottom()
        win.setGeometry(rect.adjusted(left, top, -right, -bottom))

    def _unsnap_for_drag(self, cursor: QPoint) -> None:
        """Gives a snapped window its old size back once it is dragged away."""
        size = self._restore_size
        self._restore_size = None
        if size is None or self.window.isMaximized():
            return
        # The window manager owns the position during the drag; only the size
        # is ours to change.
        self._suppress_until = time.monotonic() + 0.2
        self.window.resize(size)
