"""
Decoding images for the image tab (ui/viewers/image_tab.py) and turning them into PDFs.

decode_image(path, frame, size) returns a DecodedImage: a QImage of one frame
plus what the tab shows about the file. Pillow decodes every raster format it
reads (only the plugins in PILLOW_FORMATS are tried, so EPS never starts
Ghostscript), with the EXIF orientation applied. Files Pillow can't identify go
to QImageReader, which adds SVG, SVGZ and WBMP; an SVG is drawn at size when
one is given, so it stays sharp at any zoom. Both run on a worker thread:
QImage, unlike QPixmap, may be made off the UI thread.

Pillow's decompression-bomb check stays on. An image over twice
Image.MAX_IMAGE_PIXELS raises DecompressionBombError, which decode_image turns
into ImageError like any other failure, with a message for the person.

Frames are pages: a multi-page TIFF, the sizes in an icon file or the frames
of an animated PNG are stepped through with Page Up and Page Down. Animated GIF
and WebP play through QMovie instead (is_animation).

Modes Pillow can't hand to QImage directly are converted: 16-bit and
floating-point greyscale (scientific TIFFs, FITS, PFM) are stretched from their
own minimum and maximum to 0..255, since clipping them would show black or
white; everything else becomes RGBA.

save_as_pdf(path, output) writes every frame as one page at 72 dpi (so a page
is as many points as the image has pixels), with transparency over white. An
SVG is drawn into the PDF as vectors through QSvgRenderer.
"""

import os
from dataclasses import dataclass
from typing import Optional

from PySide6.QtCore import QMarginsF, QSize, QSizeF, Qt
from PySide6.QtGui import QImage, QImageReader, QMovie, QPageSize, QPainter, QPdfWriter

PILLOW_FORMATS = (
    "AVIF", "BLP", "BMP", "CUR", "DCX", "DDS", "DIB", "FITS", "FLI", "FTEX", "GBR", "GIF", "ICNS",
    "ICO", "IM", "JPEG", "JPEG2000", "MSP", "PCD", "PCX", "PIXAR", "PNG", "PPM", "PSD",
    "QOI", "SGI", "SUN", "TGA", "TIFF", "WEBP", "XBM", "XPM"
)
VECTOR_SUFFIXES = (".svg", ".svgz")
STRETCHED_MODES = ("I", "I;16", "I;16B", "I;16L", "I;16N", "F")


class ImageError(Exception):
    pass


@dataclass
class DecodedImage:
    image: QImage
    width: int
    height: int
    frames: int
    format: str
    vector: bool = False


def is_vector(path: str) -> bool:
    return path.lower().endswith(VECTOR_SUFFIXES)


def is_animation(path: str) -> bool:
    """True for a GIF or WebP with more than one frame, which QMovie plays."""
    reader = QImageReader(path)
    if bytes(reader.format()).decode() not in [bytes(f).decode() for f in QMovie.supportedFormats()]:
        return False
    return reader.supportsAnimation() and reader.imageCount() > 1


def _stretched(img):
    from PIL import Image

    img = img.convert("I") if img.mode.startswith("I;16") else img
    low, high = img.getextrema()
    if high <= low:
        return Image.new("L", img.size, 0)
    scale = 255.0 / (high - low)
    return img.point(lambda v: v * scale - low * scale).convert("L")


def pil_to_qimage(img) -> QImage:
    if img.mode in STRETCHED_MODES:
        img = _stretched(img)
    rgba = img.convert("RGBA")
    data = rgba.tobytes("raw", "RGBA")
    return QImage(data, rgba.width, rgba.height, rgba.width * 4, QImage.Format.Format_RGBA8888).copy()


def _decode_pillow(path: str, frame: int) -> Optional[DecodedImage]:
    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        img = Image.open(path, formats=PILLOW_FORMATS)
    except UnidentifiedImageError:
        return None
    with img:
        frames = getattr(img, "n_frames", 1) or 1
        if frame:
            img.seek(min(frame, frames - 1))
        img.load()
        name = img.format or ""
        shown = ImageOps.exif_transpose(img)
        image = pil_to_qimage(shown)
    return DecodedImage(image, image.width(), image.height(), frames, name)


def _decode_qt(path: str, frame: int, size: Optional[QSize]) -> DecodedImage:
    reader = QImageReader(path)
    reader.setAutoTransform(True)
    if not reader.canRead():
        raise ImageError("Riemann can't read this kind of image.")
    vector = is_vector(path)
    natural = reader.size()
    frames = max(1, reader.imageCount())
    if frame:
        reader.jumpToImage(min(frame, frames - 1))
    if vector and size is not None and size.isValid() and not size.isEmpty():
        reader.setScaledSize(natural.scaled(size, Qt.AspectRatioMode.KeepAspectRatio))
    image = reader.read()
    if image.isNull():
        raise ImageError(reader.errorString() or "The image is damaged.")
    width, height = (natural.width(), natural.height()) if natural.isValid() else (image.width(), image.height())
    name = bytes(reader.format()).decode().upper() or os.path.splitext(path)[1][1:].upper()
    return DecodedImage(image, width, height, frames, name, vector)


def decode_image(path: str, frame: int = 0, size: Optional[QSize] = None) -> DecodedImage:
    """One frame of path; size is how big to draw a vector image."""
    from PIL import Image

    if not os.path.isfile(path):
        raise ImageError("The file is no longer there.")
    try:
        if not is_vector(path):
            decoded = _decode_pillow(path, frame)
            if decoded is not None:
                return decoded
        return _decode_qt(path, frame, size)
    except ImageError:
        raise
    except Image.DecompressionBombError:
        raise ImageError("This image is too large to open safely.")
    except MemoryError:
        raise ImageError("There isn't enough memory to open this image.")
    except Exception as error:
        raise ImageError(f"The image is damaged or not fully supported ({error}).")


def _pillow_pages(path: str):
    from PIL import Image, ImageOps, ImageSequence

    with Image.open(path, formats=PILLOW_FORMATS) as img:
        for frame in ImageSequence.Iterator(img):
            page = ImageOps.exif_transpose(frame)
            if page.mode in STRETCHED_MODES:
                page = _stretched(page)
            rgba = page.convert("RGBA")
            flat = Image.new("RGB", rgba.size, "white")
            flat.paste(rgba, mask=rgba.getchannel("A"))
            yield flat


def _svg_to_pdf(path: str, output: str) -> None:
    from PySide6.QtSvg import QSvgRenderer

    renderer = QSvgRenderer(path)
    if not renderer.isValid():
        raise ImageError("The SVG file is damaged.")
    size = renderer.defaultSize()
    writer = QPdfWriter(output)
    writer.setResolution(72)
    writer.setPageSize(QPageSize(QSizeF(size.width(), size.height()), QPageSize.Unit.Point))
    writer.setPageMargins(QMarginsF(0, 0, 0, 0))
    painter = QPainter(writer)
    renderer.render(painter)
    painter.end()


def save_as_pdf(path: str, output: str) -> None:
    """Writes every frame of path to output as a PDF page; raises ImageError when it can't."""
    if is_vector(path):
        _svg_to_pdf(path, output)
        return
    from PIL import UnidentifiedImageError

    try:
        pages = list(_pillow_pages(path))
    except UnidentifiedImageError:
        image = decode_image(path).image
        from PIL import Image

        rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
        page = Image.frombuffer("RGBA", (rgba.width(), rgba.height()), bytes(rgba.constBits()), "raw", "RGBA", rgba.bytesPerLine(), 1)
        flat = Image.new("RGB", page.size, "white")
        flat.paste(page, mask=page.getchannel("A"))
        pages = [flat]
    except Exception as error:
        raise ImageError(f"The image couldn't be read ({error}).")
    pages[0].save(output, "PDF", resolution=72.0, save_all=True, append_images=pages[1:])
