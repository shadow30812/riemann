"""
Reading delimited text (CSV, TSV) for the table view (ui/viewers/table_tab.py).

open_delimited(path) looks at the first SAMPLE_BYTES and returns a
DelimitedFile: the encoding, the dialect and whether the first row is a header.

- Encoding: a byte-order mark decides (UTF-8, UTF-16 as Excel's "Unicode
  text" writes it); otherwise UTF-8 when the sample decodes as UTF-8 (a
  character cut off at the end of the sample is allowed), else Windows-1252,
  so old Excel exports open with their accents. Bytes the encoding can't
  decode (five bytes Windows-1252 leaves undefined, or a stray byte past the
  sample of a UTF-8 file) are read with surrogateescape and written back the
  same way, so saving an edit to one cell leaves every other byte as it was.
- Dialect: csv.Sniffer, limited to comma, semicolon, tab and bar. When it
  can't tell, the name decides: tab for .tsv and .tab, comma otherwise.
- Header: csv.Sniffer.has_header, which compares the first row's types with
  the rows under it. It says no when many cells are empty (a results table
  with gaps), and it can't read some files at all (a single column, say), so
  a first row of words over a column of mostly numbers counts as a header
  too. A file of one row has none.

DelimitedFile.rows() reads rows lazily, so the view can load a large file in
chunks as the person scrolls; a quoted field may hold line breaks. Rows are
lists of strings and may differ in length.

write_delimited(path, source, rows) saves edited rows the way the file was
written: the same encoding (a byte-order mark stays), delimiter, quote
character and line endings. Fields are quoted only where they need it, so a
file that quoted every field comes back quoted less. Text the old encoding
can't hold (an emoji in a Windows-1252 file) makes it save as UTF-8, and the
caller is told. It writes a temporary file next to the target and moves it
into place, so a failed save leaves the old file as it was, and the target
keeps its permissions.

sort_key orders a column the way a person expects: empty cells last, numbers
(with thousands separators, a sign, a percent or a currency symbol) by value
before text, and text without regard to case.
"""

import codecs
import csv
import io
import os
import re
import shutil
import tempfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

SAMPLE_BYTES = 64 * 1024
DELIMITERS = ",;\t|"
TAB_SUFFIXES = (".tsv", ".tab")
HEADER_SAMPLE_ROWS = 50
NUMBER = re.compile(r"^[-+(]?[$€£¥₹]?\s*[-+]?(\d{1,3}(,\d{3})+|\d+)(\.\d+)?([eE][-+]?\d+)?\s*%?\)?$")

csv.field_size_limit(16 * 1024 * 1024)


@dataclass
class DelimitedFile:
    path: str
    encoding: str
    dialect: type
    has_header: bool

    def rows(self) -> Iterator[list]:
        with open(self.path, encoding=self.encoding, errors="surrogateescape", newline="") as handle:
            yield from csv.reader(handle, self.dialect)


def detect_encoding(sample: bytes) -> str:
    if sample.startswith(codecs.BOM_UTF8):
        return "utf-8-sig"
    if sample.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return "utf-16"
    try:
        sample.decode("utf-8")
    except UnicodeDecodeError as error:
        if error.reason != "unexpected end of data":
            return "cp1252"
    return "utf-8"


def _fallback_dialect(path: str) -> type:
    class Fallback(csv.excel):
        delimiter = "\t" if path.lower().endswith(TAB_SUFFIXES) else ","

    return Fallback


def _complete_lines(text: str) -> str:
    cut = text.rfind("\n")
    return text[: cut + 1] if cut > 0 else text


def open_delimited(path: str) -> DelimitedFile:
    with open(path, "rb") as handle:
        raw = handle.read(SAMPLE_BYTES)
    encoding = detect_encoding(raw)
    text = _complete_lines(raw.decode(encoding, errors="replace"))
    if encoding == "utf-8-sig" and text.startswith("﻿"):
        text = text[1:]
    sniffer = csv.Sniffer()
    try:
        dialect = sniffer.sniff(text, delimiters=DELIMITERS)
        if path.lower().endswith(TAB_SUFFIXES) and dialect.delimiter != "\t" and "\t" in text:
            dialect = _fallback_dialect(path)
    except csv.Error:
        dialect = _fallback_dialect(path)
    try:
        header = text.count("\n") > 1 and sniffer.has_header(text)
    except csv.Error:
        header = False
    header = header or _numbers_under_names(text, dialect)
    return DelimitedFile(path, encoding, dialect, bool(header))


def _numbers_under_names(text: str, dialect: type) -> bool:
    """For files the Sniffer can't read: a first row of words over a column that is mostly numbers."""
    rows = [row for row in csv.reader(io.StringIO(text), dialect) if row][:HEADER_SAMPLE_ROWS]
    if len(rows) < 2 or any(number_value(cell) is not None for cell in rows[0]) or not any(c.strip() for c in rows[0]):
        return False
    for column in range(len(rows[0])):
        values = [row[column] for row in rows[1:] if column < len(row) and row[column].strip()]
        if values and sum(number_value(v) is not None for v in values) >= 0.8 * len(values):
            return True
    return False


def number_value(text: str) -> float | None:
    text = text.strip()
    if not text or not NUMBER.match(text):
        return None
    negative = text.startswith("(") and text.endswith(")")
    cleaned = re.sub(r"[^\d.eE+-]", "", text)
    try:
        value = float(cleaned)
    except ValueError:
        return None
    return -value if negative else value


def sort_key(text: str):
    text = text if isinstance(text, str) else ("" if text is None else str(text))
    if not text.strip():
        return (2, 0.0, "")
    value = number_value(text)
    if value is not None:
        return (0, value, "")
    return (1, 0.0, text.casefold())


def column_name(index: int) -> str:
    """A, B, ... Z, AA, AB: the spreadsheet name of the column at index (from 0)."""
    name = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        name = chr(65 + rest) + name
    return name


def line_ending(path: str) -> str:
    try:
        with open(path, "rb") as handle:
            sample = handle.read(SAMPLE_BYTES)
    except OSError:
        return "\n"
    if b"\r\n" in sample or b"\r\x00\n\x00" in sample:
        return "\r\n"
    return "\n"


def write_delimited(path: str, source: DelimitedFile, rows: Iterable[list]) -> str:
    """Writes rows to path as source was written; returns the encoding used."""
    rows = list(rows)

    class Written(csv.Dialect):
        delimiter = source.dialect.delimiter
        quotechar = getattr(source.dialect, "quotechar", None) or '"'
        doublequote = True
        skipinitialspace = False
        lineterminator = line_ending(source.path)
        quoting = csv.QUOTE_MINIMAL

    folder = os.path.dirname(os.path.abspath(path))
    for encoding in (source.encoding, "utf-8"):
        handle, temp = tempfile.mkstemp(prefix=".~", suffix=".tmp", dir=folder)
        try:
            with os.fdopen(handle, "w", encoding=encoding, errors="surrogateescape", newline="") as out:
                csv.writer(out, Written).writerows(rows)
        except UnicodeEncodeError:
            os.unlink(temp)
            continue
        except BaseException:
            os.unlink(temp)
            raise
        try:
            if os.path.exists(path):
                shutil.copymode(path, temp)
            else:
                mask = os.umask(0)
                os.umask(mask)
                os.chmod(temp, 0o666 & ~mask)
            os.replace(temp, path)
        except BaseException:
            os.unlink(temp)
            raise
        return encoding
    raise UnicodeError("The rows can't be written.")
