import os

from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
from pyhanko.pdf_utils.reader import PdfFileReader
from pyhanko.sign import signers
from pyhanko.sign.fields import SigFieldSpec, append_signature_field
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

from ..workers import SignatureValidationWorker


class CertificateViewerDialog(QDialog):
    """
    A dialog interface designed to display parsed X.509 Certificate details and provide export options.
    """

    def __init__(self, cert_details, parent=None):
        """
        Initializes the Certificate Viewer dialog.

        Args:
            cert_details (dict): A dictionary containing parsed certificate information mapping.
            parent: The parent widget instance handling dialog context execution.
        """
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
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Certificate", "certificate.pem", "PEM Files (*.pem)"
        )
        if path:
            try:
                with open(path, "w") as f:
                    f.write(self.cert_details.get("cert_pem", ""))
                if hasattr(self.parent(), "show_toast"):
                    self.parent().show_toast("Certificate exported successfully!")
            except Exception as e:
                QMessageBox.critical(
                    self, "Export Error", f"Could not save certificate: {e}"
                )


class SignaturesMixin:
    """
    Extends application interface providing capabilities validating, examining,
    and rendering X.509 PKCS#7 signatures encoded natively into digital documents.
    """

    def _detect_signatures(self, path: str) -> None:
        """
        Conducts a rapid structural validation to ascertain cryptographic signature objects exist.

        Args:
            path (str): The document file system pathway query payload.
        """
        try:
            with open(path, "rb") as f:
                reader = PdfFileReader(f, strict=False)
                sigs = reader.embedded_signatures
                if not sigs:
                    self.signature_banner.setVisible(False)
                    return

            self.signature_banner.setVisible(True)
            self.lbl_sig_status.setText(
                f"ℹ️ Document contains {len(sigs)} signature(s)."
            )
            self.signature_banner.setStyleSheet(
                "background-color: #1976D2; color: white;"
            )

            self.btn_trust_cert.setVisible(True)
            self.btn_trust_cert.setText("Verify Signatures")

            try:
                self.btn_trust_cert.clicked.disconnect()
            except Exception:
                pass

            self.btn_trust_cert.clicked.connect(
                lambda: self._validate_signatures(self.current_path)
            )
        except Exception as e:
            print(f"Fast signature detection failed: {e}")

    def _validate_signatures(self, path: str) -> None:
        """
        Launches an asynchronous worker executing thorough mathematical and trust chain verifications.

        Args:
            path (str): Local pathing mapping for the loaded document query container.
        """
        self.lbl_sig_status.setText("⏳ Verifying signatures... Please wait.")
        self.signature_banner.setStyleSheet("background-color: #424242; color: white;")
        self.btn_trust_cert.setVisible(False)

        trusted_pems = self.settings.value("trusted_certs_pem", [], type=list)

        self.sig_worker = SignatureValidationWorker(path, trusted_pems)
        self.sig_worker.finished_validation.connect(self._on_signatures_validated)
        self.sig_worker.start()

    def _on_signatures_validated(
        self, status: str, message: str, signatures: list
    ) -> None:
        """
        Asynchronous response parsing handler parsing structural arrays returned over Qt signaling buses.

        Args:
            status (str): Broad enum signaling verification success levels mapping integrity status.
            message (str): Formalized UI messaging parameters displaying verification statuses globally.
            signatures (list): Intact sequence lists encompassing decoded identity mapping payloads.
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
            self.signature_banner.setStyleSheet(
                "background-color: #2e7d32; color: white;"
            )
        elif status == "UNKNOWN_IDENTITY":
            self.signature_banner.setStyleSheet(
                "background-color: #f57f17; color: white;"
            )
            untrusted = next((s for s in signatures if not s["is_trusted"]), None)
            if untrusted:
                self.current_untrusted_pem = untrusted.get("cert_pem")
                self.btn_trust_cert.setVisible(True)
                self.btn_trust_cert.setText("Trust Certificate")
                try:
                    self.btn_trust_cert.clicked.disconnect()
                except Exception:
                    pass
                self.btn_trust_cert.clicked.connect(self.trust_current_certificate)
        elif status == "INVALID" or status == "ERROR":
            self.signature_banner.setStyleSheet(
                "background-color: #c62828; color: white;"
            )

        self.signatures_detected.emit(signatures)
        self._apply_signature_overlays()

    def trust_current_certificate(self) -> None:
        """
        Appends actively evaluated signature PEM credentials securely onto trusted parameter memory contexts.
        """
        if getattr(self, "current_untrusted_pem", None) is None:
            return

        trusted_pems = self.settings.value("trusted_certs_pem", [], type=list)

        if self.current_untrusted_pem not in trusted_pems:
            trusted_pems.append(self.current_untrusted_pem)
            self.settings.setValue("trusted_certs_pem", trusted_pems)
            self.show_toast("Certificate added to Trust Store.")

        if getattr(self, "current_path", None):
            self._validate_signatures(self.current_path)

    def view_certificate(self) -> None:
        """
        Triggers explicit dialog configurations showing precise identity tracking specifics internally modeled.
        """
        if not getattr(self, "current_signatures", None):
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
        Matches internally extracted physical form coordinate properties mapping onto standard screen bounds dynamically.
        """
        if not self.current_doc or not getattr(self, "current_signatures", None):
            return

        for widget in getattr(self, "page_widgets", {}).values():
            if hasattr(widget, "set_signature_overlays"):
                widget.set_signature_overlays([])

    def _populate_signatures_panel(self, signatures: list) -> None:
        """
        Constructs lateral tree representation objects linking abstract identity entities against validation statuses.

        Args:
            signatures (list): Dictionary groupings retaining status boolean parameters along identity names.
        """
        if not hasattr(self, "tree_signatures"):
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

        cert_path, _ = QFileDialog.getOpenFileName(
            self, "Select Certificate", "", "PKCS#12 Files (*.pfx *.p12)"
        )

        if cert_path:
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
        """
        Translates inputs routing through deep integration with PyHanko cryptographic libraries appending payloads efficiently.

        Args:
            cert_path (str): Locating pointer string retrieving raw certificate credential objects.
            password (str): Extracted cleartext user input validating credential usage policies locally.
        """
        try:
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
        """
        Updates explicit banner attributes projecting global document validity levels logically interpreted previously.

        Args:
            status (str): Conditional identifier modifying specific structural stylesheet rules internally evaluated.
            message (str): Literal textual feedback shown transparently communicating application findings openly.
        """
        self.signature_banner.setVisible(True)
        self.lbl_sig_status.setText(message)

        if status == "VALID":
            self.signature_banner.setStyleSheet(
                "background-color: #2e7d32; color: white;"
            )
            self.btn_trust_cert.setVisible(False)
        elif status == "UNKNOWN_IDENTITY":
            self.signature_banner.setStyleSheet(
                "background-color: #f57f17; color: white;"
            )
            self.btn_trust_cert.setVisible(True)
        elif status == "INVALID":
            self.signature_banner.setStyleSheet(
                "background-color: #c62828; color: white;"
            )
            self.btn_trust_cert.setVisible(False)
