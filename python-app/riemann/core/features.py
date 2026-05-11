import csv
import os
import zipfile

from lxml import etree  # pyright: ignore[reportAttributeAccessIssue]
from PySide6.QtCore import (
    QBuffer,
    QByteArray,
    QIODevice,
    QSettings,
    QStandardPaths,
    Qt,
    QThread,
    Signal,
)
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSlider,
    QVBoxLayout,
)


class DocumentConverter:
    """Handles format conversions using PyMuPDF and standard libraries."""

    @staticmethod
    def pdf_to_images(pdf_path: str, output_dir: str, dpi: int = 150) -> list[str]:
        """Renders PDF pages to PNG images."""
        try:
            import fitz
        except ImportError:
            raise Exception(
                "Conversion requires PyMuPDF (fitz), which is excluded from this build."
            )

        os.makedirs(output_dir, exist_ok=True)
        saved_paths = []
        base_name = os.path.splitext(os.path.basename(pdf_path))[0]

        with fitz.open(pdf_path) as doc:
            for page_num in range(len(doc)):
                page = doc.load_page(page_num)
                pix = page.get_pixmap(dpi=dpi)

                filename = f"{base_name}_page_{page_num + 1}.png"
                out_path = os.path.join(output_dir, filename)

                pix.save(out_path)
                saved_paths.append(out_path)

        return saved_paths

    @staticmethod
    def images_to_pdf(image_paths: list[str], output_path: str) -> None:
        """Combines multiple images into a single PDF."""
        try:
            import fitz
        except ImportError:
            raise Exception(
                "Conversion requires PyMuPDF (fitz), which is excluded from this build."
            )

        doc = fitz.open()
        for img_path in image_paths:
            img = fitz.open(img_path)
            rect = img[0].rect
            pdfbytes = img.convert_to_pdf()
            img.close()

            imgPDF = fitz.open("pdf", pdfbytes)
            page = doc.new_page(width=rect.width, height=rect.height)
            page.show_pdf_page(rect, imgPDF, 0)
            imgPDF.close()

        doc.save(output_path)
        doc.close()

    @staticmethod
    def pdf_to_text(pdf_path: str, output_path: str, as_markdown: bool = False) -> None:
        """Extracts text from a PDF, optionally formatting as simple Markdown."""
        try:
            import fitz
        except ImportError:
            raise Exception(
                "Conversion requires PyMuPDF (fitz), which is excluded from this build."
            )

        with fitz.open(pdf_path) as doc:
            with open(output_path, "w", encoding="utf-8") as f:
                if as_markdown:
                    f.write(f"# Extracted Content: {os.path.basename(pdf_path)}\n\n")

                for page in doc:
                    text = page.get_text("text")
                    f.write(text)
                    if as_markdown:
                        f.write("\n\n---\n\n")

    @staticmethod
    def pdf_to_html(pdf_path: str, output_path: str) -> None:
        """Extracts text and layout to raw HTML."""
        try:
            import fitz
        except ImportError:
            raise Exception(
                "Conversion requires PyMuPDF (fitz), which is excluded from this build."
            )
        with fitz.open(pdf_path) as doc:
            with open(output_path, "w", encoding="utf-8") as f:
                f.write("<html><body>\n")
                for page in doc:
                    html_content = page.get_text("html")
                    f.write(html_content)
                    f.write("<hr>\n")
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
                        f"<th style='padding: 8px; background-color: #f2f2f2;'>{h}</th>"
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
    ) -> None:
        """
        Compresses a PDF locally.
        Lossless: Cleans up internal structures, deflates streams.
        Custom: Re-encodes embedded images to JPEG at the specified quality.
        """
        try:
            import fitz
        except ImportError:
            raise Exception(
                "Compression requires PyMuPDF (fitz), which is excluded from this build."
            )
        doc = fitz.open(input_path)

        if not lossless:
            for page in doc:
                image_list = page.get_images(full=True)
                for img_info in image_list:
                    xref = img_info[0]
                    base_image = doc.extract_image(xref)

                    if base_image:
                        img_bytes = base_image["image"]
                        qimg = QImage.fromData(img_bytes)

                        if not qimg.isNull():
                            if qimg.hasAlphaChannel():
                                bg = QImage(qimg.size(), QImage.Format.Format_RGB32)
                                bg.fill(Qt.GlobalColor.white)
                                painter = QPainter(bg)
                                painter.drawImage(0, 0, qimg)
                                painter.end()
                                qimg = bg

                            buffer = QByteArray()
                            buffer_device = QBuffer(buffer)
                            buffer_device.open(QIODevice.OpenModeFlag.WriteOnly)
                            qimg.save(buffer_device, "JPEG", quality)

                            new_bytes = bytes(buffer.data())
                            original_size = doc.xref_length()

                            if len(new_bytes) < original_size:
                                page.replace_image(xref, stream=new_bytes)
                                print(
                                    f"Compressed image {xref}: {original_size // 1024}KB -> {len(new_bytes) // 1024}KB"
                                )
                            else:
                                print(
                                    f"Skipped image {xref}: New JPEG ({len(new_bytes) // 1024}KB) is larger than original ({original_size // 1024}KB)."
                                )

        doc.save(output_path, garbage=4, deflate=True)
        doc.close()


class ConvertDialog(QDialog):
    """UI Dialog for selecting and executing document conversions."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Convert Document")
        self.resize(500, 250)
        self.setMinimumSize(450, 200)

        layout = QVBoxLayout(self)

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
        btn_convert.setStyleSheet(
            "background-color: #ff4500; color: white; font-weight: bold;"
        )
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
        settings = QSettings("Riemann", "PDFReader")
        return settings.value(
            "app/last_dir",
            QStandardPaths.writableLocation(
                QStandardPaths.StandardLocation.DocumentsLocation
            ),
            type=str,
        )

    def _save_start_dir(self, path):
        if path:
            directory = os.path.dirname(path) if os.path.isfile(path) else path
            QSettings("Riemann", "PDFReader").setValue("app/last_dir", directory)

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
        btn_compress.setStyleSheet(
            "background-color: #ff4500; color: white; font-weight: bold; padding: 6px 12px;"
        )
        btn_compress.clicked.connect(self.execute_compression)
        btn_cancel = QPushButton("Cancel")
        btn_cancel.setStyleSheet("padding: 6px 12px;")
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
        settings = QSettings("Riemann", "PDFReader")
        return settings.value(
            "app/last_dir",
            QStandardPaths.writableLocation(
                QStandardPaths.StandardLocation.DocumentsLocation
            ),
            type=str,
        )

    def _save_start_dir(self, path):
        if path:
            directory = os.path.dirname(path) if os.path.isfile(path) else path
            QSettings("Riemann", "PDFReader").setValue("app/last_dir", directory)

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
            ext = os.path.splitext(in_path)[1]
            default_out = in_path.replace(ext, f"_compressed{ext}")
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
