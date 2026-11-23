"""
Images: which files count as images, decoding every format Pillow writes and
SVG, EXIF rotation, pages, the decompression-bomb limit, Save as PDF, and the
image tab inside a window (keys, folder stepping, sessions, previews, history).
"""

import os
import time

import pikepdf
import pytest
from PIL import Image
from PySide6.QtCore import QPointF, QSize, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from riemann.app import RiemannWindow
from riemann.core import images
from riemann.core.doctypes import (
    IMAGE_SUFFIXES,
    TEXT_SUFFIXES,
    dialog_filter,
    document_class,
    document_kind,
    is_reader_name,
)
from riemann.core.images import ImageError, decode_image, save_as_pdf
from riemann.ui.viewers import ImageTab
from riemann.ui.viewers.image_tab import FIT_HEIGHT, FIT_PAGE, FIT_WIDTH, folder_images

LEFT_OUT = {".eps", ".ps", ".wmf", ".emf", ".grib", ".bufr", ".h5", ".hdf", ".mpeg", ".mpg", ".iim"}
QT_ONLY = {".svg", ".svgz", ".wbmp"}
SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="50"><rect width="100" height="50" fill="blue"/></svg>'
)


def wait_for(condition, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        QApplication.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return condition()


def pillow_writable():
    Image.init()
    out = {}
    for suffix, name in Image.registered_extensions().items():
        if name in Image.SAVE and name in Image.OPEN and suffix in IMAGE_SUFFIXES:
            out.setdefault(name, suffix)
    return out


def test_image_suffixes_are_what_pillow_reads_less_the_outside_tools_plus_qt_formats():
    Image.init()
    readable = {s for s, name in Image.registered_extensions().items() if name in Image.OPEN}
    assert set(IMAGE_SUFFIXES) == (readable - LEFT_OUT) | QT_ONLY
    assert not set(IMAGE_SUFFIXES) & set(TEXT_SUFFIXES)
    assert all(name in Image.OPEN for name in images.PILLOW_FORMATS)


def test_images_are_known_by_name_and_by_signature_but_text_stays_text(tmp_path):
    photo = tmp_path / "holiday.dat"
    Image.new("RGB", (8, 8), "red").save(photo, "PNG")
    bmw = tmp_path / "cars"
    bmw.write_text("BMW and Audi\n")
    junk = tmp_path / "blob.bin"
    junk.write_bytes(b"\0\1\2\3 not an image")
    assert document_kind(str(tmp_path / "IMG_0001.JPG")) == "image"
    assert document_kind(str(tmp_path / "logo.svg")) == "image"
    assert document_kind(str(photo)) == "image"
    assert document_kind(str(bmw)) == "text"
    assert document_kind(str(junk)) == "binary"
    assert is_reader_name("scan.TIFF")
    assert document_class("scan.tiff") == "Image"
    assert document_class(str(photo)) == "Image"
    assert "*.JPG" in dialog_filter() and "*.webp" in dialog_filter()


@pytest.mark.parametrize("name,suffix", sorted(pillow_writable().items()))
def test_every_format_pillow_writes_decodes(qapp, tmp_path, name, suffix):
    path = tmp_path / f"picture{suffix}"
    source = Image.new("RGB", (48, 32), (200, 40, 40))
    for mode in ("RGB", "RGBA", "L", "P", "1"):
        try:
            source.convert(mode).save(path, name)
            break
        except (OSError, ValueError, KeyError, TypeError):
            continue
    else:
        pytest.skip(f"Pillow can't write a simple {name} file")
    assert document_kind(str(path)) == "image"
    decoded = decode_image(str(path))
    if name not in ("ICNS", "ICO"):
        assert (decoded.width, decoded.height) == (48, 32)
    assert not decoded.image.isNull()


def test_svg_is_drawn_at_the_size_asked_for(qapp, tmp_path):
    path = tmp_path / "logo.svg"
    path.write_text(SVG)
    natural = decode_image(str(path))
    big = decode_image(str(path), size=QSize(400, 400))
    assert (natural.width, natural.height) == (100, 50)
    assert natural.vector
    assert big.image.width() == 400 and big.image.height() == 200


def test_exif_orientation_is_applied(qapp, tmp_path):
    path = tmp_path / "phone.jpg"
    exif = Image.Exif()
    exif[0x0112] = 6
    Image.new("RGB", (60, 20), "white").save(path, exif=exif)
    decoded = decode_image(str(path))
    assert (decoded.width, decoded.height) == (20, 60)


def test_tiff_pages_are_frames(qapp, tmp_path):
    path = tmp_path / "scan.tif"
    pages = [Image.new("RGB", (30, 30), c) for c in ("red", "green", "blue")]
    pages[0].save(path, save_all=True, append_images=pages[1:])
    assert decode_image(str(path)).frames == 3
    assert decode_image(str(path), frame=2).image.pixelColor(1, 1).blue() == 255


def test_sixteen_bit_greyscale_is_stretched_not_clipped(qapp, tmp_path):
    path = tmp_path / "microscope.tif"
    ramp = Image.new("I;16", (256, 1))
    ramp.putdata([1000 + 10 * x for x in range(256)])
    ramp.save(path)
    decoded = decode_image(str(path))
    assert decoded.image.pixelColor(0, 0).red() == 0
    assert decoded.image.pixelColor(255, 0).red() == 255


def test_an_image_over_the_bomb_limit_is_refused(qapp, tmp_path, monkeypatch):
    path = tmp_path / "huge.png"
    Image.new("RGB", (100, 100)).save(path)
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 1000)
    with pytest.raises(ImageError, match="too large"):
        decode_image(str(path))


def test_a_damaged_image_is_an_error_not_a_crash(qapp, tmp_path):
    path = tmp_path / "broken.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR" + bytes(20))
    with pytest.raises(ImageError):
        decode_image(str(path))


def test_save_as_pdf_writes_every_page_and_keeps_svg_size(qapp, tmp_path):
    tiff = tmp_path / "scan.tif"
    pages = [Image.new("RGBA", (30, 40), (0, 0, 255, 100)) for _ in range(3)]
    pages[0].save(tiff, save_all=True, append_images=pages[1:])
    save_as_pdf(str(tiff), str(tmp_path / "scan.pdf"))
    with pikepdf.open(tmp_path / "scan.pdf") as pdf:
        assert len(pdf.pages) == 3
        assert [float(v) for v in pdf.pages[0].mediabox] == [0, 0, 30, 40]
    svg = tmp_path / "logo.svg"
    svg.write_text(SVG)
    save_as_pdf(str(svg), str(tmp_path / "logo.pdf"))
    with pikepdf.open(tmp_path / "logo.pdf") as pdf:
        box = [round(float(v)) for v in pdf.pages[0].mediabox]
        assert box[2] - box[0] == 100 and box[3] - box[1] == 50


def test_folder_images_are_in_natural_order(tmp_path):
    for name in ("img10.png", "img2.png", "IMG1.PNG", "notes.txt"):
        (tmp_path / name).write_bytes(b"")
    assert [os.path.basename(p) for p in folder_images(str(tmp_path / "img2.png"))] == [
        "IMG1.PNG",
        "img2.png",
        "img10.png",
    ]


def make_folder(tmp_path):
    first, second = tmp_path / "a.png", tmp_path / "b.png"
    Image.new("RGB", (400, 100), "red").save(first)
    Image.new("RGB", (50, 50), "green").save(second)
    return str(first), str(second)


def test_the_image_tab_fits_zooms_rotates_and_steps(qtbot, tmp_path):
    first, second = make_folder(tmp_path)
    tab = ImageTab(first)
    qtbot.addWidget(tab)
    tab.resize(800, 600)
    tab.show()
    assert wait_for(lambda: tab.decoded is not None)
    canvas = tab.canvas
    assert canvas.fit == FIT_PAGE and canvas.scale == 1.0
    QTest.keyClick(tab, Qt.Key.Key_W)
    assert canvas.fit == FIT_WIDTH and canvas.scale == pytest.approx(canvas.width() / 400)
    QTest.keyClick(tab, Qt.Key.Key_H)
    assert canvas.fit == FIT_HEIGHT
    canvas.set_scale(1.0)
    tab.zoom_in()
    assert canvas.fit is None and canvas.scale == pytest.approx(1.25)
    tab.canvas.rotate(90)
    assert canvas.rotated_size() == QSize(100, 400)
    QTest.keyClick(tab, Qt.Key.Key_Right)
    assert tab.current_path == second
    assert wait_for(lambda: tab.decoded is not None and tab.decoded.width == 50)
    QTest.keyClick(tab, Qt.Key.Key_Right)
    assert tab.current_path == first
    tab.cleanup()


def test_zooming_at_the_cursor_keeps_that_point_still(qtbot, tmp_path):
    path = tmp_path / "big.png"
    Image.new("RGB", (2000, 2000), "red").save(path)
    tab = ImageTab(str(path))
    qtbot.addWidget(tab)
    tab.resize(900, 600)
    tab.show()
    assert wait_for(lambda: tab.decoded is not None)
    canvas = tab.canvas
    canvas.set_scale(1.0)
    canvas.pan(500, 500)
    anchor = QPointF(200, 120)
    before = canvas._image_point(anchor)
    canvas.zoom_by(2.0, anchor)
    after = canvas._image_point(anchor)
    assert after.x() == pytest.approx(before.x(), abs=0.01)
    assert after.y() == pytest.approx(before.y(), abs=0.01)
    tab.cleanup()


def test_n_cycles_the_background_and_ctrl_c_copies(qtbot, tmp_path):
    first, _ = make_folder(tmp_path)
    tab = ImageTab(first, dark_mode=True)
    qtbot.addWidget(tab)
    tab.show()
    assert wait_for(lambda: tab.decoded is not None)
    assert [tab.canvas.background] + [tab.toggle_theme() or tab.canvas.background for _ in range(3)] == [
        "dark", "light", "checker", "dark"
    ]
    tab.copy_image()
    assert QApplication.clipboard().image().size() == QSize(400, 100)
    tab.cleanup()


def test_animated_gif_plays_through_qmovie(qtbot, tmp_path):
    path = tmp_path / "spin.gif"
    frames = [Image.new("RGB", (20, 20), c) for c in ("red", "green", "blue")]
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=50, loop=0)
    tab = ImageTab(str(path))
    qtbot.addWidget(tab)
    tab.show()
    assert tab.movie is not None and tab.decoded is not None
    QTest.keyClick(tab, Qt.Key.Key_Space)
    assert tab.movie.state().name == "Paused"
    tab.cleanup()
    assert tab.movie is None


def test_the_window_opens_images_in_image_tabs_and_keeps_them_in_the_session(qtbot, tmp_path):
    first, second = make_folder(tmp_path)
    window = RiemannWindow(incognito=False, restore_session=False)
    qtbot.addWidget(window)
    tab = window._open_document(first, preview=False)
    assert isinstance(tab, ImageTab) and not tab.is_preview
    assert window.history_manager.get_model_data()
    entry = [e for e in window._serialize_tab_state(window.tabs_main) if e.get("data") == os.path.abspath(first)]
    assert entry == [{"type": "doc", "data": os.path.abspath(first), "preview": False}]

    window.close_tab(window.tabs_main.indexOf(tab))
    window.restore_last_closed_tab()
    reopened = window.tabs_main.currentWidget()
    assert isinstance(reopened, ImageTab) and reopened.current_path == os.path.abspath(first)

    window._on_file_single_clicked(second)
    preview = window._live_preview_tab()
    assert isinstance(preview, ImageTab) and preview.is_preview
    window._on_file_double_clicked(second)
    full = window.tabs_main.currentWidget()
    assert isinstance(full, ImageTab) and not full.is_preview
    for widget in (reopened, full):
        widget.cleanup()


def test_a_running_riemann_accepts_images_from_another_launch(tmp_path):
    from riemann.window.instance import accepts_handoff

    photo = tmp_path / "photo.webp"
    Image.new("RGB", (4, 4)).save(photo)
    assert accepts_handoff(str(photo))
