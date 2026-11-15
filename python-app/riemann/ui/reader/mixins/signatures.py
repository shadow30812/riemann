"""
Notes:

_detect_signatures: PDFium reads the count straight from the AcroForm. pyhanko
parsed the whole cross-reference table on the UI thread first, which froze the
window for about 0.7 s on large files.
"""

import os
import tempfile

import certifi
from PySide6.QtGui import QImage
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QTreeWidgetItem,
    QVBoxLayout,
)

from ..render_worker import PAGE_IMAGE_FORMAT
from ..workers import SignatureValidationWorker
from ....core.log import log
from ....core.settings import app_settings, get_dialog_directory, save_last_directory
from ...palette import banner_style, tokens
from .host import ReaderHostBase


class CertificateViewerDialog(QDialog):
    """
    A dialog interface designed to display parsed X.509 Certificate details and provide export options.
    """

    def __init__(self, cert_details, parent=None):
        super().__init__(parent)
        self.cert_details = cert_details
        self.setWindowTitle("Certificate Viewer")
        self.resize(1000, 300)

        self.setSizeGripEnabled(True)
        layout = QVBoxLayout(self)
        form = QFormLayout()

        form.addRow(
            "Subject:", QLabel(cert_details.get("subject", "N/A"), wordWrap=True)
        )
        form.addRow("Issuer:", QLabel(cert_details.get("issuer", "N/A"), wordWrap=True))
        form.addRow("Serial Number:", QLabel(cert_details.get("serial", "N/A")))
        form.addRow("Valid From:", QLabel(cert_details.get("not_before", "N/A")))
        form.addRow("Valid To:", QLabel(cert_details.get("not_after", "N/A")))

        hash_text = QTextEdit(cert_details.get("cert_hash", "N/A"))
        hash_text.setReadOnly(True)
        hash_text.setMinimumHeight(50)
        hash_text.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )

        form.addRow("SHA-256 Fingerprint:", hash_text)
        layout.addLayout(form)

        self.btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.btn_export = QPushButton("Export (.pem)")
        self.btn_export.clicked.connect(self.export_cert)
        self.btn_box.addButton(self.btn_export, QDialogButtonBox.ButtonRole.ActionRole)

        if not cert_details.get("is_trusted"):
            self.btn_trust = QPushButton("Trust this Certificate")
            self.btn_box.addButton(
                self.btn_trust, QDialogButtonBox.ButtonRole.ActionRole
            )

        self.btn_box.rejected.connect(self.reject)
        self.btn_box.accepted.connect(self.accept)
        layout.addWidget(self.btn_box)

    def export_cert(self):
        """
        Triggers a local filesystem saving routine outputting the certificate's PEM format string blocks.
        """
        settings = app_settings()
        start_dir = get_dialog_directory(settings)

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Certificate",
            os.path.join(start_dir, "certificate.pem"),
            "PEM Files (*.pem)",
        )
        if path:
            save_last_directory(settings, path)
            try:
                with open(path, "w") as f:
                    f.write(self.cert_details.get("cert_pem", ""))
                if hasattr(self.parent(), "show_toast"):
                    self.parent().show_toast("Certificate exported successfully!")
            except Exception as e:
                QMessageBox.critical(
                    self, "Export Error", f"Could not save certificate: {e}"
                )


class SignaturesMixin(ReaderHostBase):
    current_signatures = None
    signature_appearances = None
    current_untrusted_pem = None
    tree_signatures = None

    _signature_banner_kind = "neutral"

    def _style_signature_banner(self, kind: str) -> None:
        self._signature_banner_kind = kind
        self.signature_banner.setStyleSheet(banner_style(tokens(self.theme_mode != 0), kind))


    def _detect_signatures(self, path: str) -> None:
        """Counts the signatures quickly and offers to verify them."""
        try:
            doc = self.current_doc
            if doc is not None and hasattr(doc, "signature_count"):
                count = doc.signature_count()
            else:
                from pyhanko.pdf_utils.reader import PdfFileReader

                with open(path, "rb") as f:
                    count = len(PdfFileReader(f, strict=False).embedded_signatures)
            if not count:
                self.signature_banner.setVisible(False)
                return

            self.signature_banner.setVisible(True)
            self.lbl_sig_status.setText(
                f"ℹ️ Document contains {count} signature(s)."
            )
            self._style_signature_banner("accent")

            self.btn_trust_cert.setVisible(True)
            self.btn_trust_cert.setText("Verify Signatures")

            try:
                self.btn_trust_cert.clicked.disconnect()
            except Exception:
                log.debug("_detect_signatures failed", exc_info=True)

            self.btn_trust_cert.clicked.connect(
                lambda: self._validate_signatures(self.current_path)
            )
        except Exception as e:
            print(f"Fast signature detection failed: {e}")

    def _validate_signatures(self, path: str) -> None:
        """Verifies the signatures and their trust chains on a worker thread."""
        self.lbl_sig_status.setText("⏳ Verifying signatures... Please wait.")
        self._style_signature_banner("neutral")
        self.btn_trust_cert.setVisible(False)

        trusted_pems = self.settings.value("trusted_certs_pem", [], type=list)

        try:
            with open(certifi.where(), "r", encoding="utf-8") as f:
                ca_bundle = f.read()

            mozilla_pems = [
                pem.strip() + "\n-----END CERTIFICATE-----\n"
                for pem in ca_bundle.split("-----END CERTIFICATE-----")
                if "-----BEGIN CERTIFICATE-----" in pem
            ]

            trusted_pems.extend(mozilla_pems)
        except Exception as e:
            print(f"Failed to load Mozilla Root CAs: {e}")

        self.sig_worker = SignatureValidationWorker(path, trusted_pems)
        self.sig_worker.finished_validation.connect(self._on_signatures_validated)
        self.sig_worker.start()

    def _on_signatures_validated(
        self, status: str, message: str, signatures: list
    ) -> None:
        """
        Shows the verification result. status is VALID, UNKNOWN_IDENTITY, INVALID, ERROR
        or NONE.
        """
        self.current_signatures = signatures
        if status == "NONE":
            self.signature_banner.setVisible(False)
            self.signatures_detected.emit([])
            return

        self.signature_banner.setVisible(True)
        self.lbl_sig_status.setText(message)
        self.btn_trust_cert.setVisible(False)
        self.current_untrusted_pem = None

        if status == "VALID":
            self._style_signature_banner("success")
        elif status == "UNKNOWN_IDENTITY":
            self._style_signature_banner("warning")
            untrusted = next((s for s in signatures if not s["is_trusted"]), None)
            if untrusted:
                self.current_untrusted_pem = untrusted.get("cert_pem")
                self.btn_trust_cert.setVisible(True)
                self.btn_trust_cert.setText("Trust Certificate")
                try:
                    self.btn_trust_cert.clicked.disconnect()
                except Exception:
                    log.debug("_on_signatures_validated failed", exc_info=True)
                self.btn_trust_cert.clicked.connect(self.trust_current_certificate)
        elif status == "INVALID" or status == "ERROR":
            self._style_signature_banner("danger")

        self.signatures_detected.emit(signatures)
        self._apply_signature_overlays()

    def trust_current_certificate(self) -> None:
        if self.current_untrusted_pem is None:
            return

        trusted_pems = self.settings.value("trusted_certs_pem", [], type=list)

        if self.current_untrusted_pem not in trusted_pems:
            trusted_pems.append(self.current_untrusted_pem)
            self.settings.setValue("trusted_certs_pem", trusted_pems)
            self.show_toast("Certificate added to Trust Store.")

        if self.current_path:
            self._validate_signatures(self.current_path)

    def view_certificate(self) -> None:
        if not self.current_signatures:
            self.show_toast("No signature data available.")
            return

        target_cert = next(
            (s for s in self.current_signatures if not s["is_trusted"]),
            self.current_signatures[0],
        )

        dialog = CertificateViewerDialog(target_cert, self)

        if hasattr(dialog, "btn_trust"):
            dialog.btn_trust.clicked.connect(
                lambda: [self.trust_current_certificate(), dialog.accept()]
            )

        dialog.exec()

    def _apply_signature_overlays(self) -> None:
        """
        Shows each signature field's validation result on the page, in place
        of the "validity unknown" layer stored in the file.
        """
        by_page = {}
        for sig in self.current_signatures or []:
            for appearance in sig.get("appearances", []):
                by_page.setdefault(appearance["page"], []).append(dict(appearance, images={}))
        if by_page == {} and not self.signature_appearances:
            return
        changed = set(by_page) | set(self.signature_appearances or {})
        self.signature_appearances = by_page
        for page in changed:
            self.refresh_page_render(page)

    def _signature_appearance_image(self, appearance: dict, width_px: int):
        """Renders a status appearance with PDFium, cached per size and theme."""
        key = (width_px, self.theme_mode)
        image = appearance["images"].get(key)
        if image is not None or self.engine is None:
            return image
        fd, path = tempfile.mkstemp(suffix=".pdf")
        doc = None
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(appearance["pdf"])
            doc = self.engine.load_document(path, None)
            width_pt = doc.get_page_size(0)[0] or 1.0
            res = doc.render_page(0, width_px / width_pt, self.theme_mode)
            image = QImage(res.data, res.width, res.height, res.width * 4, PAGE_IMAGE_FORMAT).copy()
        except Exception as e:
            print(f"Could not render signature appearance: {e}")
            image = QImage()
        finally:
            del doc
            try:
                os.remove(path)
            except OSError:
                pass
        appearance["images"] = {key: image}
        return image

    def _populate_signatures_panel(self, signatures: list) -> None:
        """Lists each signer and their status in the side panel."""
        if self.tree_signatures is None:
            print("Warning: tree_signatures widget not found in ReaderTab UI.")
            return

        self.tree_signatures.clear()

        for sig in signatures:
            icon = "✔️" if sig["valid"] else "❌"
            item = QTreeWidgetItem(self.tree_signatures)
            item.setText(0, f"{icon} {sig['subject']}")
            item.setText(1, sig["field_name"])

            child_cert = QTreeWidgetItem(item)
            child_cert.setText(0, f"Cert Hash: {sig['cert_hash']}")

            if not sig["valid"]:
                child_warn = QTreeWidgetItem(item)
                child_warn.setText(0, "Warning: Document Altered!")

        self.tree_signatures.expandAll()

    def initiate_signing_flow(self) -> None:
        """
        Instantiates specific dialog controls gathering sensitive input for subsequent PKCS12 manipulations.
        """
        if not self.current_path:
            QMessageBox.warning(self, "Sign Error", "No document loaded.")
            return

        start_dir = get_dialog_directory(self.settings)
        cert_path, _ = QFileDialog.getOpenFileName(
            self, "Select Certificate", start_dir, "PKCS#12 Files (*.pfx *.p12)"
        )

        if cert_path:
            save_last_directory(self.settings, cert_path)
            password, ok = QInputDialog.getText(
                self,
                "Certificate Password",
                "Enter password for the certificate:",
                QLineEdit.EchoMode.Password,
            )
            if ok and password:
                self.show_toast("Initiating pyHanko signing flow...")
                self.execute_pyhanko_signing(cert_path, password)

    def execute_pyhanko_signing(self, cert_path: str, password: str) -> None:
        try:
            from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
            from pyhanko.sign import signers
            from pyhanko.sign.fields import SigFieldSpec, append_signature_field

            signer = signers.SimpleSigner.load_pkcs12(cert_path, password.encode())
            output_path = self.current_path.replace(".pdf", "_signed.pdf")

            with open(self.current_path, "rb") as doc:
                writer = IncrementalPdfFileWriter(doc)

                field_name = f"Signature_{os.urandom(4).hex()}"
                append_signature_field(
                    writer,
                    SigFieldSpec(
                        sig_field_name=field_name,
                        on_page=0,
                        box=(10, 10, 200, 60),
                    ),
                )

                with open(output_path, "wb") as out_f:
                    signers.sign_pdf(
                        writer,
                        signers.PdfSignatureMetadata(field_name=field_name),
                        signer=signer,
                        out=out_f,
                    )

            self.show_toast(
                f"Document securely signed! Saved as {os.path.basename(output_path)}"
            )
            self.load_document(output_path)

        except Exception as e:
            QMessageBox.critical(
                self, "Signing Error", f"Failed to sign document:\n{str(e)}"
            )

    def update_signature_banner(self, status: str, message: str) -> None:
        self.signature_banner.setVisible(True)
        self.lbl_sig_status.setText(message)

        if status == "VALID":
            self._style_signature_banner("success")
            self.btn_trust_cert.setVisible(False)
        elif status == "UNKNOWN_IDENTITY":
            self._style_signature_banner("warning")
            self.btn_trust_cert.setVisible(True)
        elif status == "INVALID":
            self._style_signature_banner("danger")
            self.btn_trust_cert.setVisible(False)
