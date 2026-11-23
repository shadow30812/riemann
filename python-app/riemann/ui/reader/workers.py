"""
Background workers for the reader: the LaTeX OCR model download and install,
model loading and inference, OCR of one page or many, and metadata
extraction.
"""

import hashlib
import os
import re
import subprocess
import urllib.request
import zipfile
from typing import Any

from PySide6.QtCore import QThread, Signal

from ...core import ai_engine
from ...core.dependencies import LATEX_OCR_LOCK, pip_install_command, setup_and_get_venv
from ...core.endpoints import CROSSREF_WORKS, OPENALEX_WORKS, USER_AGENT, loopback_url
from ...core.log import log
from ...core.processes import NO_WINDOW
from ...core.settings import AI_TAGS_KEY, OCR_LANGUAGES_KEY, app_settings, string_list

METADATA_TIMEOUT = (3.0, 5.0)


class DownloadCancelled(Exception):
    pass


class ModelDownloader(QThread):
    """
    Downloads the LaTeX OCR model pack, checks it against a pinned SHA-256 and
    unpacks it into dest_folder.

    The pack is added to sys.path afterwards, so anything in it runs as
    Riemann; a file whose hash differs from expected_sha256 is deleted unread.
    Archive members that would land outside dest_folder are refused. cancel()
    stops the transfer at the next chunk.
    """

    progress = Signal(int)
    finished = Signal(bool)

    CHUNK = 1 << 20

    def __init__(self, url: str, dest_folder: str, expected_sha256: str) -> None:
        super().__init__()
        self.url = url
        self.dest_folder = dest_folder
        self.expected_sha256 = expected_sha256.lower()
        self.error = ""
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        zip_path = os.path.join(self.dest_folder, "latex_ocr.zip.part")
        try:
            os.makedirs(self.dest_folder, exist_ok=True)
            digest = self._download(zip_path)
            if digest != self.expected_sha256:
                raise ValueError(
                    "The downloaded model pack does not match the expected checksum "
                    f"(got {digest[:12]}…, expected {self.expected_sha256[:12]}…)."
                )
            self.progress.emit(99)
            safe_extract(zip_path, self.dest_folder)
            self.finished.emit(True)
        except DownloadCancelled:
            self.error = "Download cancelled."
            self.finished.emit(False)
        except Exception as e:
            self.error = str(e)
            self.finished.emit(False)
        finally:
            try:
                os.remove(zip_path)
            except OSError:
                pass

    def _download(self, zip_path: str) -> str:
        sha = hashlib.sha256()
        with urllib.request.urlopen(self.url, timeout=60) as response, open(zip_path, "wb") as out:
            total = int(response.headers.get("Content-Length") or 0)
            done = 0
            while True:
                if self._cancelled:
                    raise DownloadCancelled()
                chunk = response.read(self.CHUNK)
                if not chunk:
                    break
                out.write(chunk)
                sha.update(chunk)
                done += len(chunk)
                if total:
                    self.progress.emit(min(98, done * 100 // total))
        return sha.hexdigest()


def safe_extract(zip_path: str, dest_folder: str) -> None:
    """
    Unpacks zip_path into dest_folder. Raises ValueError if a member's path would leave
    dest_folder.
    """
    root = os.path.realpath(dest_folder)
    with zipfile.ZipFile(zip_path) as archive:
        for member in archive.infolist():
            target = os.path.realpath(os.path.join(root, member.filename))
            if target != root and not target.startswith(root + os.sep):
                raise ValueError(f"Unsafe path in model pack: {member.filename}")
        archive.extractall(root)


def tag_request(text_chunk: str) -> dict:
    """The /tag payload: the start of the text and the user's subject labels, if set."""
    payload: dict = {"text_chunk": text_chunk[:1500]}
    tags = string_list(AI_TAGS_KEY)
    if tags:
        payload["tags"] = tags
    return payload


class InstallerThread(QThread):

    finished_install = Signal()
    install_error = Signal(str)

    def run(self) -> None:
        """
        Invokes core module subprocess installations targeting python packages required for inference.
        """
        try:
            subprocess.check_call(pip_install_command(setup_and_get_venv(), LATEX_OCR_LOCK))
            self.finished_install.emit()
        except Exception as e:
            self.install_error.emit(f"Installer Error: {e}")


class LoaderThread(QThread):

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
            self.error_occurred.emit(f"AI Initialization Failed:\n{e!s}")


def available_ocr_languages() -> list[str]:
    """The languages the installed Tesseract can read ([] when it is missing)."""
    try:
        out = subprocess.run(
            ["tesseract", "--list-langs"], capture_output=True, text=True, timeout=10, **NO_WINDOW
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return sorted(line.strip() for line in out.splitlines()[1:] if line.strip() and line.strip() != "osd")


class OcrThread(QThread):
    """
    Recognises the text of one page with Tesseract (through the Rust core,
    which releases the GIL), off the UI thread, in the languages chosen in
    Settings (OCR_LANGUAGES_KEY, "eng" by default).
    """

    finished_ocr = Signal(int, str)
    error_occurred = Signal(str)

    def __init__(self, doc: Any, page_index: int, scale: float = 2.0, languages: str | None = None) -> None:
        super().__init__()
        self.doc = doc
        self.page_index = page_index
        self.scale = scale
        self.languages = languages or str(app_settings().value(OCR_LANGUAGES_KEY, "eng") or "eng")

    def run(self) -> None:
        try:
            text = self.doc.ocr_page(self.page_index, self.scale, self.languages)
        except Exception as e:
            self.error_occurred.emit(str(e) or type(e).__name__)
            return
        self.finished_ocr.emit(self.page_index, text)


class OcrPagesThread(QThread):
    """
    Recognises the text of many pages, BATCH pages per Tesseract process
    (RiemannDocument.ocr_pages), and reports progress after each batch. A
    failure or requestInterruption() stops the run after the current batch;
    finished_ocr then carries the pages read so far and the reason.
    """

    BATCH = 8

    progress = Signal(int, int)
    finished_ocr = Signal(list, str)

    def __init__(self, doc: Any, pages: list[int], scale: float = 2.0, languages: str | None = None) -> None:
        super().__init__()
        self.doc = doc
        self.pages = list(pages)
        self.scale = scale
        self.languages = languages or str(app_settings().value(OCR_LANGUAGES_KEY, "eng") or "eng")

    def run(self) -> None:
        results: list[tuple[int, str]] = []
        problem = ""
        for start in range(0, len(self.pages), self.BATCH):
            if self.isInterruptionRequested():
                problem = "Cancelled"
                break
            batch = self.pages[start : start + self.BATCH]
            try:
                texts = self.doc.ocr_pages(batch, self.scale, self.languages)
            except Exception as e:
                problem = str(e) or type(e).__name__
                break
            results.extend(zip(batch, texts, strict=False))
            self.progress.emit(len(results), len(self.pages))
        self.finished_ocr.emit(results, problem)


class PageTextThread(QThread):
    """The text of every page of doc, for the AI engine; finished_text carries (path, pages)."""

    finished_text = Signal(str, list)
    error_occurred = Signal(str)

    def __init__(self, doc: Any, path: str) -> None:
        super().__init__()
        self.doc = doc
        self.path = path

    def run(self) -> None:
        try:
            pages = [self.doc.get_page_text(i) for i in range(self.doc.page_count)]
        except Exception as e:
            self.error_occurred.emit(str(e) or type(e).__name__)
            return
        self.finished_text.emit(self.path, pages)


class InferenceThread(QThread):

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
            self.error_occurred.emit(f"Inference Error: {e!s}")


class SignatureValidationWorker(QThread):

    finished_validation = Signal(str, str, list)

    def __init__(self, pdf_path: str, trusted_certs: list, parent=None):
        super().__init__(parent)
        self.pdf_path = pdf_path
        self.trusted_certs = trusted_certs or []

    def run(self):
        try:
            from asn1crypto import pem, x509
            from pyhanko.pdf_utils.reader import PdfFileReader
            from pyhanko.sign.validation import validate_pdf_signature
            from pyhanko_certvalidator import ValidationContext
            from pyhanko_certvalidator.policy_decl import DisallowWeakAlgorithmsPolicy

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
                    log.debug("run failed", exc_info=True)

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

                self._attach_status_appearances(sig_details)

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
                "ERROR", f"Error reading signatures: {e!s}", []
            )


    def _attach_status_appearances(self, sig_details: list) -> None:
        """
        Adds the appearance each field should show for its result (see
        signature_appearance). Fields whose identity is unknown keep their
        stored look, which already says so.
        """
        from riemann.core.signature_appearance import INVALID, VALID, status_appearances

        statuses = {}
        for sig in sig_details:
            if not sig["valid"]:
                statuses[sig["field_name"]] = INVALID
            elif sig["is_trusted"]:
                statuses[sig["field_name"]] = VALID
        try:
            appearances = status_appearances(self.pdf_path, statuses)
        except Exception as e:
            print(f"Could not build signature appearances: {e}")
            return
        for sig in sig_details:
            sig["appearances"] = [
                a for a in appearances
                if a["field"] == sig["field_name"] or a["field"].endswith("." + sig["field_name"])
            ]


class MetadataExtractionWorker(QThread):

    finished_extraction = Signal(dict)

    def __init__(self, text_chunk: str, parent=None):
        super().__init__(parent)
        self.text_chunk = text_chunk
        self.headers = {"User-Agent": USER_AGENT}

    def run(self) -> None:
        import requests

        metadata = {}

        with requests.Session() as session:
            doi_match = re.search(
                r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", self.text_chunk, re.IGNORECASE
            )
            if doi_match:
                doi = doi_match.group(0).rstrip(".")
                metadata["doi"] = doi
                try:
                    res = session.get(
                        f"{CROSSREF_WORKS}/{doi}",
                        headers=self.headers,
                        timeout=METADATA_TIMEOUT,
                    )
                    if res.status_code == 200:
                        data = res.json().get("message", {})
                        metadata["title"] = data.get("title", [""])[0]
                        authors = [
                            f"{a.get('given', '')} {a.get('family', '')}".strip()
                            for a in data.get("author", [])
                        ]
                        metadata["authors"] = ", ".join(filter(None, authors))

                        try:
                            bib_res = session.get(
                                f"{CROSSREF_WORKS}/{doi}",
                                headers={
                                    "Accept": "application/x-bibtex",
                                    "User-Agent": USER_AGENT,
                                },
                                timeout=METADATA_TIMEOUT,
                            )
                            if bib_res.status_code == 200:
                                metadata["bibtex"] = bib_res.text
                        except Exception:
                            log.debug("run failed", exc_info=True)

                        self.finished_extraction.emit(metadata)
                        self.quit()
                        return
                except Exception:
                    log.debug("run failed", exc_info=True)

            if self.isInterruptionRequested():
                return
            arxiv_match = re.search(
                r"arXiv:(\d{4}\.\d{4,5})", self.text_chunk, re.IGNORECASE
            )
            if arxiv_match:
                arxiv_id = arxiv_match.group(1)
                metadata["arxiv_id"] = arxiv_id
                try:
                    res = session.get(
                        f"{OPENALEX_WORKS}/arxiv:{arxiv_id}",
                        headers=self.headers,
                        timeout=METADATA_TIMEOUT,
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
                    log.debug("run failed", exc_info=True)

        lines = [line.strip() for line in self.text_chunk.split("\n") if line.strip()]
        if len(lines) >= 2:
            metadata["title"] = lines[0]
            metadata["authors"] = lines[1]

        if self.isInterruptionRequested():
            return
        engine = ai_engine.connection()
        if engine is not None:
            try:
                tag_res = requests.post(
                    loopback_url(engine[0], "tag"),
                    json=tag_request(self.text_chunk),
                    headers={"X-Riemann-Token": engine[1]},
                    timeout=2.0,
                )
                if tag_res.status_code == 200:
                    metadata["tags"] = tag_res.json().get("tags", [])
            except Exception:
                log.debug("run failed", exc_info=True)

        self.finished_extraction.emit(metadata)
