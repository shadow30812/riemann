"""
Document conversion, compression and local caption generation.

Conversions read PDFs through the Rust core (riemann_core, on PDFium): pages
are rendered for PDF to images, and text comes from the same extraction the
reader uses. Images become a PDF with Pillow, one page per image at 72 dpi
(so a page is as many points as the image has pixels), with transparency
flattened onto white. PDF compression uses pikepdf: lossless mode drops unused
objects, packs objects into object streams and recompresses Flate streams;
lossy mode also re-encodes each image XObject once as JPEG when that is
smaller, leaving stencil masks, images under 8 bits per channel, and any
soft mask (transparency) as they were. No AGPL code (PyMuPDF) is involved.
"""

import csv
import html
import os
import subprocess
import zipfile

from lxml import etree  # pyright: ignore[reportAttributeAccessIssue]
from PySide6.QtCore import (
    QStandardPaths,
    Qt,
    QThread,
    Signal,
)
from PySide6.QtGui import QImage
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QRadioButton,
    QSlider,
    QVBoxLayout,
)
from .log import log
from .processes import NO_WINDOW
from .settings import app_settings, get_dialog_directory, save_last_directory
from ..ui.palette import LIGHT, dialog_icon
from .settings import DEFAULT_DIR_KEY, LAST_DIR_KEY


class DocumentConverter:
    @staticmethod
    def pdf_to_images(pdf_path: str, output_dir: str, dpi: int = 150) -> list[str]:
        """Renders each page to a PNG at dpi; returns the files written."""
        doc = _open_pdf(pdf_path)
        os.makedirs(output_dir, exist_ok=True)
        base_name = unused_image_base(output_dir, os.path.splitext(os.path.basename(pdf_path))[0], doc.page_count)
        saved_paths = []
        for page_num in range(doc.page_count):
            res = doc.render_page(page_num, dpi / 72.0, 0)
            image = QImage(res.data, res.width, res.height, res.width * 4, QImage.Format.Format_RGB32)
            out_path = os.path.join(output_dir, f"{base_name}_page_{page_num + 1}.png")
            if not image.save(out_path, "PNG"):
                raise OSError(f"Could not write {out_path}")
            saved_paths.append(out_path)
        return saved_paths

    @staticmethod
    def images_to_pdf(image_paths: list[str], output_path: str) -> None:
        """One page per image, each page the image's size at 72 dpi."""
        from PIL import Image

        if not image_paths:
            raise ValueError("No images to combine.")
        pages = []
        for path in image_paths:
            with Image.open(path) as img:
                if img.mode in ("RGBA", "LA", "P"):
                    img = img.convert("RGBA")
                    flat = Image.new("RGB", img.size, "white")
                    flat.paste(img, mask=img.getchannel("A"))
                    pages.append(flat)
                else:
                    pages.append(img.convert("RGB") if img.mode not in ("RGB", "L") else img.copy())
        pages[0].save(output_path, "PDF", resolution=72.0, save_all=True, append_images=pages[1:])

    @staticmethod
    def pdf_to_text(pdf_path: str, output_path: str, as_markdown: bool = False) -> None:
        doc = _open_pdf(pdf_path)
        with open(output_path, "w", encoding="utf-8") as f:
            if as_markdown:
                f.write(f"# Extracted Content: {os.path.basename(pdf_path)}\n\n")
            for page_num in range(doc.page_count):
                f.write(doc.get_page_text(page_num))
                if as_markdown:
                    f.write("\n\n---\n\n")

    @staticmethod
    def pdf_to_html(pdf_path: str, output_path: str) -> None:
        """Each page as a box of its size in points, with the text placed where it sits on the page."""
        doc = _open_pdf(pdf_path)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("<!DOCTYPE html>\n<html><head><meta charset='utf-8'><style>"
                    f".page{{position:relative;margin:12px auto;border:1px solid {LIGHT['border_strong']};overflow:hidden}}"
                    ".page span{position:absolute;white-space:pre;line-height:1}"
                    "</style></head><body>\n")
            for page_num in range(doc.page_count):
                width, height = doc.get_page_size(page_num)
                f.write(f"<div class='page' style='width:{width:.1f}pt;height:{height:.1f}pt'>\n")
                for text, (left, top, right, bottom) in doc.get_text_segments(page_num):
                    if not text.strip():
                        continue
                    size = max(1.0, top - bottom)
                    f.write(
                        f"<span style='left:{left:.1f}pt;top:{height - top:.1f}pt;font-size:{size:.1f}pt'>"
                        f"{html.escape(text)}</span>\n"
                    )
                f.write("</div>\n")
            f.write("</body></html>\n")

    @staticmethod
    def csv_to_html(csv_path: str) -> str:
        """Converts a CSV file to a formatted HTML table string."""
        html = [
            "<table border='1' style='border-collapse: collapse; width: 100%; font-family: sans-serif;'>"
        ]
        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            try:
                headers = next(reader)
                html.append(
                    "<tr>"
                    + "".join(
                        f"<th style='padding: 8px; background-color: {LIGHT['raised']};'>{h}</th>"
                        for h in headers
                    )
                    + "</tr>"
                )
                for row in reader:
                    html.append(
                        "<tr>"
                        + "".join(
                            f"<td style='padding: 8px;'>{cell}</td>" for cell in row
                        )
                        + "</tr>"
                    )
            except StopIteration:
                pass
        html.append("</table>")
        return "\n".join(html)

    @staticmethod
    def epub_to_html(epub_path: str) -> str:
        """Extracts and concatenates all HTML chapters from an EPUB."""
        html_parts = []
        with zipfile.ZipFile(epub_path, "r") as archive:
            container_xml = archive.read("META-INF/container.xml")
            container_tree = etree.fromstring(container_xml)
            opf_path = container_tree.find(
                ".//{urn:oasis:names:tc:opendocument:xmlns:container}rootfile"
            ).get("full-path")
            opf_dir = os.path.dirname(opf_path)

            opf_content = archive.read(opf_path)
            opf_tree = etree.fromstring(opf_content)
            ns = {"opf": "http://www.idpf.org/2007/opf"}

            manifest = {
                item.get("id"): item.get("href")
                for item in opf_tree.xpath("//opf:item", namespaces=ns)
            }
            spine = [
                item.get("idref")
                for item in opf_tree.xpath("//opf:itemref", namespaces=ns)
            ]

            for item_id in spine:
                href = manifest.get(item_id)
                if href:
                    full_href = os.path.join(opf_dir, href).replace("\\", "/")
                    try:
                        chapter_html = archive.read(full_href).decode("utf-8")
                        html_parts.append(chapter_html)
                        html_parts.append("<hr style='page-break-after: always;'>")
                    except KeyError:
                        continue

        return "\n".join(html_parts)


class DocumentCompressor:
    """Handles local compression of images and PDFs."""

    @staticmethod
    def compress_image(
        input_path: str, output_path: str, lossless: bool, quality: int = 50
    ) -> bool:
        """Compresses an image natively using Qt."""
        img = QImage(input_path)
        if img.isNull():
            return False

        if lossless:
            return img.save(output_path, quality=100)
        else:
            return img.save(output_path, "JPEG", quality=quality)

    @staticmethod
    def compress_pdf(
        input_path: str, output_path: str, lossless: bool, quality: int = 50
    ) -> int:
        """
        Lossless: drops unused objects, packs objects into streams and
        recompresses Flate streams. Otherwise also re-encodes embedded images as
        JPEG at quality, each image once (pages can share one), keeping the
        original wherever the JPEG would not be smaller. Returns the number of
        images re-encoded.
        """
        import pikepdf

        replaced = 0
        with pikepdf.open(input_path) as pdf:
            if not lossless:
                done = set()
                for page in pdf.pages:
                    for _name, obj in page.images.items():
                        key = obj.objgen
                        if key in done:
                            continue
                        done.add(key)
                        if _reencode_image(pdf, obj, quality):
                            replaced += 1
            pdf.remove_unreferenced_resources()
            pdf.save(
                output_path,
                compress_streams=True,
                recompress_flate=True,
                object_stream_mode=pikepdf.ObjectStreamMode.generate,
            )
        return replaced


def _open_pdf(path: str):
    import riemann_core

    return riemann_core.PdfEngine().load_document(path, None)


def _reencode_image(pdf, obj, quality: int) -> bool:
    """Replaces one image XObject with a JPEG if that is smaller. Its soft mask, if any, stays."""
    import io

    import pikepdf
    from PIL import Image

    if obj.get("/ImageMask", False) or int(obj.get("/BitsPerComponent", 8)) < 8:
        return False
    try:
        pil = pikepdf.PdfImage(obj).as_pil_image()
    except Exception:
        log.debug("Image %s not decodable; kept", obj.objgen, exc_info=True)
        return False
    if pil.mode not in ("RGB", "L"):
        if "A" in pil.getbands() or pil.mode == "P":
            pil = pil.convert("RGBA")
            flat = Image.new("RGB", pil.size, "white")
            flat.paste(pil, mask=pil.getchannel("A"))
            pil = flat
        else:
            pil = pil.convert("RGB")
    buf = io.BytesIO()
    pil.save(buf, "JPEG", quality=quality)
    data = buf.getvalue()
    original = len(obj.read_raw_bytes())
    if len(data) >= original:
        log.info("Kept image %s: JPEG %d KB is not smaller than %d KB", obj.objgen, len(data) // 1024, original // 1024)
        return False
    obj.write(data, filter=pikepdf.Name.DCTDecode)
    obj.ColorSpace = pikepdf.Name.DeviceGray if pil.mode == "L" else pikepdf.Name.DeviceRGB
    obj.BitsPerComponent = 8
    for key in ("/DecodeParms", "/Decode", "/Intent"):
        if key in obj:
            del obj[key]
    log.info("Compressed image %s: %d KB -> %d KB", obj.objgen, original // 1024, len(data) // 1024)
    return True


class ConvertDialog(QDialog):
    """UI Dialog for selecting and executing document conversions."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Convert Document")
        self.resize(500, 250)
        self.setMinimumSize(450, 200)

        layout = QVBoxLayout(self)
        dialog_icon(self, "file-output")

        layout.addWidget(QLabel("Select Conversion Type:"))
        self.combo_type = QComboBox()
        self.combo_type.addItems(
            [
                "PDF to Images (PNG)",
                "PDF to Markdown / Text",
                "PDF to HTML",
                "Images to PDF",
                "CSV to HTML Table",
                "EPUB to HTML",
            ]
        )
        layout.addWidget(self.combo_type)

        in_layout = QHBoxLayout()
        self.txt_input = QLineEdit()
        self.txt_input.setPlaceholderText("Select input file(s)...")
        self.txt_input.setReadOnly(True)
        btn_browse_in = QPushButton("Browse")
        btn_browse_in.clicked.connect(self.browse_input)
        in_layout.addWidget(self.txt_input)
        in_layout.addWidget(btn_browse_in)
        layout.addWidget(QLabel("Input:"))
        layout.addLayout(in_layout)

        out_layout = QHBoxLayout()
        self.txt_output = QLineEdit()
        self.txt_output.setPlaceholderText("Select output destination...")
        self.txt_output.setReadOnly(True)
        btn_browse_out = QPushButton("Browse")
        btn_browse_out.clicked.connect(self.browse_output)
        out_layout.addWidget(self.txt_output)
        out_layout.addWidget(btn_browse_out)
        layout.addWidget(QLabel("Output:"))
        layout.addLayout(out_layout)

        btn_layout = QHBoxLayout()
        btn_convert = QPushButton("Convert")
        btn_convert.setProperty("role", "primary")
        btn_convert.clicked.connect(self.execute_conversion)
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)

        btn_layout.addStretch()
        btn_layout.addWidget(btn_cancel)
        btn_layout.addWidget(btn_convert)
        layout.addStretch()
        layout.addLayout(btn_layout)

        self.input_paths = []

    def _get_start_dir(self):
        return get_dialog_directory(app_settings())

    def _save_start_dir(self, path):
        save_last_directory(app_settings(), path)

    def browse_input(self):
        start_dir = self._get_start_dir()
        idx = self.combo_type.currentIndex()
        if idx == 3:
            files, _ = QFileDialog.getOpenFileNames(
                self, "Select Images", start_dir, "Images (*.png *.jpg *.jpeg)"
            )
            if files:
                self._save_start_dir(files[0])
                self.input_paths = files
                self.txt_input.setText(f"{len(files)} images selected")
        else:
            filters = [
                "PDF (*.pdf)",
                "PDF (*.pdf)",
                "PDF (*.pdf)",
                "",
                "CSV (*.csv)",
                "EPUB (*.epub)",
            ]
            file, _ = QFileDialog.getOpenFileName(
                self, "Select Input File", start_dir, filters[idx]
            )
            if file:
                self._save_start_dir(file)
                self.input_paths = [file]
                self.txt_input.setText(os.path.basename(file))

    def browse_output(self):
        start_dir = self._get_start_dir()
        idx = self.combo_type.currentIndex()
        if idx == 0:
            directory = QFileDialog.getExistingDirectory(
                self, "Select Output Directory", start_dir
            )
            if directory:
                self._save_start_dir(directory)
                self.txt_output.setText(directory)
        else:
            filters = [
                "",
                "Markdown (*.md)",
                "HTML (*.html)",
                "PDF (*.pdf)",
                "HTML (*.html)",
                "HTML (*.html)",
            ]

            default_name = ""
            if self.input_paths:
                base = os.path.splitext(os.path.basename(self.input_paths[0]))[0]
                ext_map = {1: ".md", 2: ".html", 3: ".pdf", 4: ".html", 5: ".html"}
                default_name = os.path.join(start_dir, base + ext_map.get(idx, ""))
            else:
                default_name = start_dir

            file, _ = QFileDialog.getSaveFileName(
                self, "Save Output As", default_name, filters[idx]
            )
            if file:
                self._save_start_dir(file)
                self.txt_output.setText(file)

    def execute_conversion(self):
        if not self.input_paths or not self.txt_output.text():
            QMessageBox.warning(
                self, "Missing Info", "Please select both input and output paths."
            )
            return

        self.btn_convert = self.sender()
        self.btn_convert.setText("Converting...")
        self.btn_convert.setEnabled(False)

        self.worker = ConversionWorker(
            self.combo_type.currentIndex(),
            self.input_paths[0],
            self.txt_output.text(),
            self.input_paths,
        )
        self.worker.finished.connect(self._on_success)
        self.worker.error.connect(self._on_error)
        self.worker.start()

    def _on_success(self):
        QMessageBox.information(self, "Success", "Conversion completed successfully!")
        self.accept()

    def _on_error(self, err_msg):
        self.btn_convert.setText("Convert")
        self.btn_convert.setEnabled(True)
        QMessageBox.critical(self, "Error", f"Conversion failed:\n{err_msg}")


class CompressDialog(QDialog):
    """UI Dialog for compressing PDFs and Images."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Compress Document")
        self.resize(550, 350)
        self.setMinimumSize(500, 300)
        layout = QVBoxLayout(self)
        dialog_icon(self, "file-archive")

        in_layout = QHBoxLayout()
        self.txt_input = QLineEdit()
        self.txt_input.setPlaceholderText("Select PDF or Image...")
        self.txt_input.setReadOnly(True)
        btn_browse_in = QPushButton("Browse")
        btn_browse_in.clicked.connect(self.browse_input)
        in_layout.addWidget(self.txt_input)
        in_layout.addWidget(btn_browse_in)
        layout.addWidget(QLabel("Input File:"))
        layout.addLayout(in_layout)

        layout.addSpacing(10)
        layout.addWidget(QLabel("Compression Mode:"))
        self.group = QButtonGroup(self)

        self.radio_lossless = QRadioButton("Lossless (Safe, minor size reduction)")
        self.radio_custom = QRadioButton("Custom Quality (Lossy, shrinks images)")
        self.radio_lossless.setChecked(True)

        self.group.addButton(self.radio_lossless, 1)
        self.group.addButton(self.radio_custom, 2)
        self.radio_lossless.toggled.connect(self._toggle_slider)

        layout.addWidget(self.radio_lossless)
        layout.addWidget(self.radio_custom)

        slider_layout = QHBoxLayout()
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(1, 100)
        self.slider.setValue(50)
        self.slider.setEnabled(False)
        self.slider.valueChanged.connect(self._update_lbl)

        self.lbl_val = QLabel("Quality: 50%")
        self.lbl_val.setFixedWidth(80)
        self.lbl_val.setEnabled(False)

        slider_layout.addSpacing(25)
        slider_layout.addWidget(self.slider)
        slider_layout.addWidget(self.lbl_val)
        layout.addLayout(slider_layout)

        layout.addSpacing(10)

        out_layout = QHBoxLayout()
        self.txt_output = QLineEdit()
        self.txt_output.setPlaceholderText("Save compressed file as...")
        self.txt_output.setReadOnly(True)
        btn_browse_out = QPushButton("Browse")
        btn_browse_out.clicked.connect(self.browse_output)
        out_layout.addWidget(self.txt_output)
        out_layout.addWidget(btn_browse_out)
        layout.addWidget(QLabel("Output File:"))
        layout.addLayout(out_layout)

        btn_layout = QHBoxLayout()
        btn_compress = QPushButton("Compress")
        btn_compress.setProperty("role", "primary")
        btn_compress.clicked.connect(self.execute_compression)
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)

        btn_layout.addStretch()
        btn_layout.addWidget(btn_cancel)
        btn_layout.addWidget(btn_compress)
        layout.addStretch()
        layout.addLayout(btn_layout)

    def _toggle_slider(self):
        is_custom = self.radio_custom.isChecked()
        self.slider.setEnabled(is_custom)
        self.lbl_val.setEnabled(is_custom)

    def _update_lbl(self, val):
        self.lbl_val.setText(f"Quality: {val}%")

    def _get_start_dir(self):
        return get_dialog_directory(app_settings())

    def _save_start_dir(self, path):
        save_last_directory(app_settings(), path)

    def browse_input(self):
        start_dir = self._get_start_dir()
        file, _ = QFileDialog.getOpenFileName(
            self,
            "Select File to Compress",
            start_dir,
            "Documents (*.pdf *.png *.jpg *.jpeg)",
        )
        if file:
            self._save_start_dir(file)
            self.txt_input.setText(file)

    def browse_output(self):
        in_path = self.txt_input.text()
        start_dir = self._get_start_dir()

        if in_path:
            stem, ext = os.path.splitext(in_path)
            default_out = unused_path(f"{stem}_compressed{ext}")
        else:
            default_out = start_dir
            ext = ".*"

        file, _ = QFileDialog.getSaveFileName(
            self, "Save Compressed File", default_out, f"Files (*{ext})"
        )
        if file:
            self._save_start_dir(file)
            self.txt_output.setText(file)

    def execute_compression(self):
        in_path = self.txt_input.text()
        out_path = self.txt_output.text()

        if not in_path or not out_path:
            QMessageBox.warning(
                self, "Missing Info", "Please select input and output files."
            )
            return

        self.btn_compress = self.sender()
        self.btn_compress.setText("Compressing...")
        self.btn_compress.setEnabled(False)

        self.worker = CompressionWorker(
            in_path,
            out_path,
            self.radio_lossless.isChecked(),
            self.slider.value(),
            os.path.splitext(in_path)[1].lower(),
        )
        self.worker.finished.connect(self._on_success)
        self.worker.error.connect(self._on_error)
        self.worker.start()

    def _on_success(self):
        QMessageBox.information(self, "Success", "File compressed successfully!")
        self.accept()

    def _on_error(self, err_msg):
        self.btn_compress.setText("Compress")
        self.btn_compress.setEnabled(True)
        QMessageBox.critical(self, "Error", f"Compression failed:\n{err_msg}")


class ConversionWorker(QThread):
    finished = Signal()
    error = Signal(str)

    def __init__(self, idx, in_path, out_path, input_paths=None):
        super().__init__()
        self.idx = idx
        self.in_path = in_path
        self.out_path = out_path
        self.input_paths = input_paths

    def run(self):
        try:
            if self.idx == 0:
                DocumentConverter.pdf_to_images(self.in_path, self.out_path)
            elif self.idx == 1:
                DocumentConverter.pdf_to_text(
                    self.in_path, self.out_path, as_markdown=True
                )
            elif self.idx == 2:
                DocumentConverter.pdf_to_html(self.in_path, self.out_path)
            elif self.idx == 3:
                DocumentConverter.images_to_pdf(self.input_paths, self.out_path)
            elif self.idx == 4:
                html_str = DocumentConverter.csv_to_html(self.in_path)
                with open(self.out_path, "w", encoding="utf-8") as f:
                    f.write(html_str)
            elif self.idx == 5:
                html_str = DocumentConverter.epub_to_html(self.in_path)
                with open(self.out_path, "w", encoding="utf-8") as f:
                    f.write(html_str)
            self.finished.emit()
        except Exception as e:
            self.error.emit(str(e))


class CompressionWorker(QThread):
    finished = Signal()
    error = Signal(str)

    def __init__(self, in_path, out_path, lossless, quality, ext):
        super().__init__()
        self.in_path = in_path
        self.out_path = out_path
        self.lossless = lossless
        self.quality = quality
        self.ext = ext

    def run(self):
        try:
            if self.ext == ".pdf":
                DocumentCompressor.compress_pdf(
                    self.in_path, self.out_path, self.lossless, self.quality
                )
            else:
                success = DocumentCompressor.compress_image(
                    self.in_path, self.out_path, self.lossless, self.quality
                )
                if not success:
                    raise Exception("Image processing failed.")
            self.finished.emit()
        except Exception as e:
            self.error.emit(str(e))


class CaptionGeneratorWorker(QThread):
    progress = Signal(str)
    finished = Signal(bool, str)

    def __init__(self, video_path):
        super().__init__()
        self.video_path = video_path

    def format_timestamp(self, seconds: float) -> str:
        """Formats seconds into the standard SRT timestamp format."""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int((seconds - int(seconds)) * 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

    def run(self):
        try:
            from faster_whisper import WhisperModel

            self.progress.emit("Loading Whisper 'base' model...")
            model = WhisperModel(
                "base", device="cpu", compute_type="int8", cpu_threads=8
            )

            self.progress.emit("Transcribing audio (this may take a few minutes)...")

            segments, info = model.transcribe(
                self.video_path,
                beam_size=1,
                task="translate",
                vad_filter=True,
                condition_on_previous_text=False,
            )

            srt_path = unused_path(os.path.splitext(self.video_path)[0] + ".srt")

            self.progress.emit("Writing subtitle file...")
            with open(srt_path, "w", encoding="utf-8") as f:
                for i, segment in enumerate(segments, start=1):
                    start = self.format_timestamp(segment.start)
                    end = self.format_timestamp(segment.end)
                    f.write(f"{i}\n{start} --> {end}\n{segment.text.strip()}\n\n")

            self.progress.emit("Embedding subtitles into video container...")
            output_path = unused_path(os.path.splitext(self.video_path)[0] + "_captioned.mp4")

            cmd = [
                "ffmpeg",
                "-n",
                "-i",
                self.video_path,
                "-i",
                srt_path,
                "-c",
                "copy",
                "-c:s",
                "mov_text",
                output_path,
            ]

            subprocess.run(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, **NO_WINDOW
            )

            self.finished.emit(True, f"Successfully created:\n{output_path}\n{srt_path}")

        except ImportError:
            self.finished.emit(False, "faster-whisper is not installed.")
        except subprocess.CalledProcessError:
            self.finished.emit(
                False, "FFmpeg failed. Ensure it is installed on your system."
            )
        except Exception as e:
            self.finished.emit(False, f"An error occurred: {str(e)}")


def unused_path(path: str) -> str:
    """path, or "name (2).ext", "name (3).ext"... when it already exists."""
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(path)
    n = 2
    while os.path.exists(f"{stem} ({n}){ext}"):
        n += 1
    return f"{stem} ({n}){ext}"


def unused_image_base(folder: str, base: str, pages: int) -> str:
    """base, or "base (2)", "base (3)"... so that none of the page images base_page_N.png exists in folder yet."""
    candidate, n = base, 2
    while any(os.path.exists(os.path.join(folder, f"{candidate}_page_{i}.png")) for i in range(1, pages + 1)):
        candidate = f"{base} ({n})"
        n += 1
    return candidate


def trigger_local_caption_generation(parent_window):
    """
    Opens a file dialog, respects app settings, and triggers the background captioning worker.
    """
    store = app_settings()
    default_dir = store.value(DEFAULT_DIR_KEY, "", type=str)

    if not default_dir or not os.path.exists(default_dir):
        default_dir = store.value(
            LAST_DIR_KEY, "", type=str
        ) or QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.MoviesLocation
        )

    video_path, _ = QFileDialog.getOpenFileName(
        parent_window,
        "Select Video for Captioning",
        default_dir,
        "Video Files (*.mp4 *.mkv *.avi *.mov *.webm);;All Files (*)",
    )

    if not video_path:
        return

    store.setValue(LAST_DIR_KEY, os.path.dirname(video_path))

    progress_dialog = QProgressDialog("Initializing...", None, 0, 0, parent_window)
    progress_dialog.setWindowTitle("Generating Captions")
    progress_dialog.setModal(True)
    progress_dialog.setMinimumDuration(0)

    progress_dialog.setCancelButton(None)
    worker = CaptionGeneratorWorker(video_path)

    parent_window._caption_worker = worker
    parent_window._caption_dialog = progress_dialog

    worker.progress.connect(progress_dialog.setLabelText)

    def on_finished(success, message):
        progress_dialog.close()
        if success:
            QMessageBox.information(parent_window, "Success", message)
        else:
            QMessageBox.warning(parent_window, "Error", message)

    worker.finished.connect(on_finished)

    worker.start()
    progress_dialog.show()
