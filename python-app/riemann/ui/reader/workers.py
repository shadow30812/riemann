"""
Background Workers for the Reader Module.
"""

import hashlib
import os
import re
import subprocess
import sys
import urllib.request
import zipfile
from typing import Any

import requests
from asn1crypto import pem, x509
from pyhanko.pdf_utils.reader import PdfFileReader
from pyhanko.sign.validation import validate_pdf_signature
from pyhanko_certvalidator import ValidationContext
from pyhanko_certvalidator.policy_decl import DisallowWeakAlgorithmsPolicy
from PySide6.QtCore import QThread, Signal


class ModelDownloader(QThread):
    """Downloads and extracts the LaTeX OCR model pack."""

    progress = Signal(int)
    finished = Signal(bool)

    def __init__(self, url: str, dest_folder: str) -> None:
        super().__init__()
        self.url = url
        self.dest_folder = dest_folder

    def run(self) -> None:
        try:
            os.makedirs(self.dest_folder, exist_ok=True)
            zip_path = os.path.join(self.dest_folder, "latex_ocr.zip")

            def report(block_num: int, block_size: int, total_size: int) -> None:
                if total_size > 0:
                    percent = int((block_num * block_size * 100) / total_size)
                    self.progress.emit(percent)

            urllib.request.urlretrieve(self.url, zip_path, report)
            self.progress.emit(99)

            with zipfile.ZipFile(zip_path, "r") as zip_ref:
                zip_ref.extractall(self.dest_folder)

            os.remove(zip_path)
            self.finished.emit(True)
        except Exception as e:
            print(f"Download/Extraction failed: {e}")
            self.finished.emit(False)


class InstallerThread(QThread):
    """Runs pip install in background."""

    finished_install = Signal()
    install_error = Signal(str)

    def run(self) -> None:
        try:
            cmd = [
                sys.executable,
                "-m",
                "pip",
                "install",
                "pix2tex[gui]",
                "torch",
                "torchvision",
            ]
            subprocess.check_call(cmd)
            self.finished_install.emit()
        except Exception as e:
            self.install_error.emit(f"Installer Error: {e}")


class LoaderThread(QThread):
    """Initializes the AI model."""

    finished_loading = Signal(object)
    error_occurred = Signal(str)

    def run(self) -> None:
        try:
            from pix2tex.cli import LatexOCR

            model = LatexOCR()
            self.finished_loading.emit(model)
        except ImportError:
            self.error_occurred.emit("Module 'pix2tex' not found.")
        except Exception as e:
            self.error_occurred.emit(f"AI Initialization Failed:\n{str(e)}")


class InferenceThread(QThread):
    """Runs the LaTeX OCR inference in the background."""

    finished_inference = Signal(str)
    error_occurred = Signal(str)

    def __init__(self, model: Any, image: Any) -> None:
        super().__init__()
        self.model = model
        self.image = image

    def run(self) -> None:
        try:
            code = self.model(self.image)
            self.finished_inference.emit(code)
        except Exception as e:
            self.error_occurred.emit(f"Inference Error: {str(e)}")


class SignatureValidationWorker(QThread):
    finished_validation = Signal(str, str, list)

    def __init__(self, pdf_path: str, trusted_certs: list, parent=None):
        super().__init__(parent)
        self.pdf_path = pdf_path
        self.trusted_certs = trusted_certs or []

    def run(self):
        try:
            relaxed_policy = DisallowWeakAlgorithmsPolicy(
                weak_hash_algos=set(), weak_signature_algos=set()
            )

            trust_roots = []
            for cert_str in self.trusted_certs:
                try:
                    if isinstance(cert_str, str) and "BEGIN CERTIFICATE" in cert_str:
                        _, _, der_bytes = pem.unarmor(cert_str.encode("utf-8"))
                        trust_roots.append(x509.Certificate.load(der_bytes))
                except Exception:
                    pass

            vc = ValidationContext(
                extra_trust_roots=trust_roots,
                allow_fetching=True,
                algorithm_usage_policy=relaxed_policy,
            )

            with open(self.pdf_path, "rb") as f:
                reader = PdfFileReader(f, strict=False)
                embedded_sigs = reader.embedded_signatures

                if not embedded_sigs:
                    self.finished_validation.emit("NONE", "No signatures.", [])
                    return

                all_valid = True
                all_trusted = True
                sig_details = []

                for sig in embedded_sigs:
                    cert = getattr(sig, "signer_cert", None)
                    valid = False
                    is_trusted = False

                    try:
                        status = validate_pdf_signature(
                            sig, signer_validation_context=vc
                        )
                        valid = getattr(status, "intact", False)
                        is_trusted = getattr(status, "trusted", False)
                    except Exception:
                        try:
                            fallback = validate_pdf_signature(sig)
                            valid = getattr(fallback, "intact", False)
                        except Exception:
                            valid = False

                    if cert:
                        cert_hash = hashlib.sha256(cert.dump()).hexdigest()
                        cert_pem_str = pem.armor("CERTIFICATE", cert.dump()).decode(
                            "ascii"
                        )
                        subject = cert.subject.human_friendly
                        issuer = getattr(cert.issuer, "human_friendly", "Unknown")
                        serial = hex(cert.serial_number)

                        # Direct trust override for end-entity certs lacking issuers
                        if not is_trusted and cert_pem_str in self.trusted_certs:
                            is_trusted = True

                        try:
                            validity = cert["tbs_certificate"]["validity"]
                            not_before = validity["not_before"].native.strftime(
                                "%Y-%m-%d %H:%M:%S Z"
                            )
                            not_after = validity["not_after"].native.strftime(
                                "%Y-%m-%d %H:%M:%S Z"
                            )
                        except Exception:
                            not_before = "N/A"
                            not_after = "N/A"
                    else:
                        cert_hash = ""
                        cert_pem_str = ""
                        subject = "Unknown Identity"
                        issuer = "Unknown"
                        serial = "N/A"
                        not_before = "N/A"
                        not_after = "N/A"

                    all_valid = all_valid and valid
                    all_trusted = all_trusted and is_trusted

                    sig_details.append(
                        {
                            "field_name": getattr(sig, "field_name", "Unknown"),
                            "subject": subject,
                            "issuer": issuer,
                            "serial": serial,
                            "not_before": not_before,
                            "not_after": not_after,
                            "valid": valid,
                            "cert_hash": cert_hash,
                            "cert_pem": cert_pem_str,
                            "is_trusted": is_trusted,
                        }
                    )

                if not all_valid:
                    self.finished_validation.emit(
                        "INVALID",
                        "🟥 Signature is INVALID. Document modified.",
                        sig_details,
                    )
                elif all_valid and all_trusted:
                    self.finished_validation.emit(
                        "VALID",
                        "🟩 Signed and all signatures are valid and trusted.",
                        sig_details,
                    )
                else:
                    self.finished_validation.emit(
                        "UNKNOWN_IDENTITY",
                        "🟨 Signed, but certificate validity is unknown.",
                        sig_details,
                    )
        except Exception as e:
            self.finished_validation.emit(
                "ERROR", f"Error reading signatures: {str(e)}", []
            )


class MetadataExtractionWorker(QThread):
    finished_extraction = Signal(dict)

    def __init__(self, text_chunk: str, parent=None):
        super().__init__(parent)
        self.text_chunk = text_chunk
        self.headers = {"User-Agent": "RiemannReader/1.0"}

    def run(self) -> None:
        metadata = {}

        # FIX: Use a Session block to aggressively release sockets/CPU
        with requests.Session() as session:
            doi_match = re.search(
                r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", self.text_chunk, re.IGNORECASE
            )
            if doi_match:
                doi = doi_match.group(0).rstrip(".")
                metadata["doi"] = doi
                try:
                    # FIX: Strict (Connect, Read) timeout tuple prevents indefinite hanging
                    res = session.get(
                        f"https://api.crossref.org/works/{doi}",
                        headers=self.headers,
                        timeout=(3.0, 5.0),
                    )
                    if res.status_code == 200:
                        data = res.json().get("message", {})
                        metadata["title"] = data.get("title", [""])[0]
                        authors = [
                            f"{a.get('given', '')} {a.get('family', '')}".strip()
                            for a in data.get("author", [])
                        ]
                        metadata["authors"] = ", ".join(filter(None, authors))
                        self.finished_extraction.emit(metadata)
                        self.quit()
                        return
                except Exception:
                    pass

            arxiv_match = re.search(
                r"arXiv:(\d{4}\.\d{4,5})", self.text_chunk, re.IGNORECASE
            )
            if arxiv_match:
                arxiv_id = arxiv_match.group(1)
                metadata["arxiv_id"] = arxiv_id
                try:
                    res = session.get(
                        f"https://api.openalex.org/works/arxiv:{arxiv_id}",
                        headers=self.headers,
                        timeout=(3.0, 5.0),
                    )
                    if res.status_code == 200:
                        data = res.json()
                        metadata["title"] = data.get("title", "")
                        metadata["year"] = str(data.get("publication_year", ""))
                        authors = [
                            a.get("author", {}).get("display_name", "")
                            for a in data.get("authorships", [])
                        ]
                        metadata["authors"] = ", ".join(filter(None, authors))
                        self.finished_extraction.emit(metadata)
                        self.quit()
                        return
                except Exception:
                    pass

        lines = [line.strip() for line in self.text_chunk.split("\n") if line.strip()]
        if len(lines) >= 2:
            metadata["title"] = lines[0]
            metadata["authors"] = lines[1]

        self.finished_extraction.emit(metadata)
