from unittest.mock import MagicMock, patch

import pytest
from riemann.ui.reader.mixins.signatures import SignaturesMixin


class DummySignaturesReader(SignaturesMixin):
    def __init__(self):
        self.current_path = "/fake/doc.pdf"
        self.current_signatures = []
        self.current_untrusted_pem = None
        self.current_doc = MagicMock()

        self.signature_banner = MagicMock()
        self.lbl_sig_status = MagicMock()
        self.btn_trust_cert = MagicMock()
        self.settings = MagicMock()
        self.signatures_detected = MagicMock()
        self.tree_signatures = MagicMock()

    def show_toast(self, msg):
        self.last_toast = msg

    def load_document(self, path):
        pass


@pytest.fixture
def reader():
    return DummySignaturesReader()


@patch("riemann.ui.reader.mixins.signatures.PdfFileReader")
@patch("builtins.open", new_callable=MagicMock)
def test_detect_signatures_found(mock_open, mock_pdf_reader, reader):
    mock_reader_instance = MagicMock()
    mock_reader_instance.embedded_signatures = ["sig1", "sig2"]
    mock_pdf_reader.return_value = mock_reader_instance

    reader._detect_signatures("/fake.pdf")

    reader.signature_banner.setVisible.assert_called_with(True)
    assert "2 signature(s)" in reader.lbl_sig_status.setText.call_args[0][0]
    reader.btn_trust_cert.setVisible.assert_called_with(True)


@patch(
    "riemann.ui.reader.mixins.signatures.certifi.where", return_value="/fake/cert.pem"
)
@patch("builtins.open", new_callable=MagicMock)
@patch("riemann.ui.reader.mixins.signatures.SignatureValidationWorker")
def test_validate_signatures(mock_worker_cls, mock_open, mock_where, reader):
    mock_open.return_value.__enter__.return_value.read.return_value = ""
    reader.settings.value.return_value = ["trusted_pem_data"]

    reader._validate_signatures("/fake.pdf")

    mock_worker_cls.assert_called_with("/fake.pdf", ["trusted_pem_data"])
    mock_worker_cls.return_value.start.assert_called_once()
    assert reader.btn_trust_cert.setVisible.call_args[0][0] is False


def test_on_signatures_validated_valid(reader):
    mock_sigs = [{"valid": True, "is_trusted": True, "subject": "John Doe"}]

    reader._on_signatures_validated("VALID", "All signatures valid.", mock_sigs)

    assert reader.current_signatures == mock_sigs
    reader.signature_banner.setStyleSheet.assert_called_with(
        "background-color: #2e7d32; color: white;"
    )
    reader.signatures_detected.emit.assert_called_with(mock_sigs)


def test_on_signatures_validated_unknown(reader):
    mock_sigs = [
        {
            "valid": True,
            "is_trusted": False,
            "subject": "Unknown Entity",
            "cert_pem": "pem_data",
        }
    ]

    reader._on_signatures_validated(
        "UNKNOWN_IDENTITY", "Identity not trusted.", mock_sigs
    )

    assert reader.current_untrusted_pem == "pem_data"
    reader.btn_trust_cert.setVisible.assert_called_with(True)
    assert reader.btn_trust_cert.setText.call_args[0][0] == "Trust Certificate"


@patch.object(DummySignaturesReader, "_validate_signatures")
def test_trust_current_certificate(mock_validate, reader):
    reader.current_untrusted_pem = "new_untrusted_pem"
    reader.settings.value.return_value = ["old_pem"]

    reader.trust_current_certificate()

    reader.settings.setValue.assert_called_with(
        "trusted_certs_pem", ["old_pem", "new_untrusted_pem"]
    )
    assert "Trust Store" in reader.last_toast
    mock_validate.assert_called_with("/fake/doc.pdf")


@patch("riemann.ui.reader.mixins.signatures.QLineEdit", create=True)
@patch("riemann.ui.reader.mixins.signatures.QFileDialog")
@patch("riemann.ui.reader.mixins.signatures.QInputDialog")
@patch.object(DummySignaturesReader, "execute_pyhanko_signing")
def test_initiate_signing_flow(
    mock_execute, mock_input, mock_file, mock_line_edit, reader
):
    mock_file.getOpenFileName.return_value = ("/keys/cert.pfx", "")
    mock_input.getText.return_value = ("mypassword", True)

    reader.initiate_signing_flow()

    mock_execute.assert_called_once_with("/keys/cert.pfx", "mypassword")


@patch("riemann.ui.reader.mixins.signatures.signers")
@patch("riemann.ui.reader.mixins.signatures.IncrementalPdfFileWriter")
@patch("riemann.ui.reader.mixins.signatures.append_signature_field")
@patch("builtins.open", new_callable=MagicMock)
def test_execute_pyhanko_signing(
    mock_open, mock_append, mock_writer, mock_signers, reader
):
    mock_signer = MagicMock()
    mock_signers.SimpleSigner.load_pkcs12.return_value = mock_signer

    reader.execute_pyhanko_signing("/keys/cert.pfx", "password")

    mock_signers.SimpleSigner.load_pkcs12.assert_called_once_with(
        "/keys/cert.pfx", b"password"
    )
    mock_append.assert_called_once()
    mock_signers.sign_pdf.assert_called_once()
    assert "securely signed" in reader.last_toast
