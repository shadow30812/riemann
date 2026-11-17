"""
What kind of document a file is, decided in one place for the whole app.

document_kind(path) returns "pdf", "markdown", "html", "ebook", "office",
"image", "table", "text" or "binary":

- a name ending in .pdf, or a file starting with %PDF-, is a PDF;
- .md and .markdown are Markdown; .html, .htm and .xhtml are HTML, which opens
  in the browser;
- .csv and .tsv (TABLE_SUFFIXES) are tables, shown as a grid;
- .epub is an e-book, and so is a zip whose first entry is a mimetype file
  saying application/epub+zip, as the EPUB format requires;
- a name in OFFICE_SUFFIXES is an Office file (Word, PowerPoint, Excel,
  OpenDocument, RTF, Apple iWork), and so is a file that starts with {\rtf, a
  zip holding Office Open XML parts (word/, ppt/ or xl/), or a zip whose
  mimetype entry names an OpenDocument type. office_type(path) says which:
  document, presentation, spreadsheet or drawing. .dot and .pot are left out:
  those names are far more often Graphviz graphs and gettext templates;
- a name in IMAGE_SUFFIXES is an image: the formats Pillow reads, less those
  that need outside programs or only identify a file (EPS, WMF and EMF, GRIB,
  BUFR, HDF5, MPEG, IPTC), plus SVG and WBMP, which only Qt reads;
- any other file is text when its first SNIFF_BYTES hold no NUL byte and decode
  as UTF-8 (a character cut off at the end of the sample is allowed), so .py,
  .cpp, .log, Makefile and files with no extension all count;
- a file that isn't text but starts with one of IMAGE_SIGNATURES is an image
  whatever its name. Text is tested first, so a text file that happens to
  start with "BM" stays text. Everything else is binary, and the app refuses
  to open it.

The reader shows PDFs, Markdown, text and e-books (READER_KINDS), and a table
as text when asked ("Open as text"); an e-book is turned into one page first
(core/epub.py); images and tables open in the tabs of
ui/viewers (VIEWER_KINDS). TAB_KINDS are the kinds that open in a document tab. TEXT_SUFFIXES and TEXT_NAMES (the
prose and code lists together) name the common text and source-code files.
They are used where reading the file is too slow or not trusted: the explorer's icons, the open dialogs'
filter, and window/instance.py, which decides what a running Riemann opens
when another process hands it a path (a security boundary, so it never
sniffs content).

document_class(path) sorts the Documents history into DOCUMENT_CLASSES (PDF,
Markdown, E-book, Spreadsheet, Code, Text, Image): CODE_SUFFIXES and CODE_NAMES are source, build and
style files; PROSE_SUFFIXES and PROSE_NAMES are prose, data, configuration
and logs, which count as Text. It goes by name first, since history lists
files that may be gone, and reads only a file whose name says nothing.
"""

import os

SNIFF_BYTES = 8192
MARKDOWN_SUFFIXES = (".md", ".markdown")
HTML_SUFFIXES = (".html", ".htm", ".xhtml")
DOCUMENT_CLASSES = ("PDF", "Word", "Slides", "Spreadsheet", "Markdown", "E-book", "Code", "Text", "Image")
READER_KINDS = ("pdf", "markdown", "text", "ebook", "office")
CONVERTED_KINDS = ("ebook",)
VIEWER_KINDS = ("image", "table")
TAB_KINDS = READER_KINDS + VIEWER_KINDS
TABLE_SUFFIXES = (".csv", ".tsv")
EBOOK_SUFFIXES = (".epub",)
EPUB_MIMETYPE = b"mimetypeapplication/epub+zip"

OFFICE_TYPES = {
    "document": (
        ".doc", ".docx", ".docm", ".dotx", ".dotm", ".odt", ".ott", ".fodt", ".rtf", ".wpd", ".pages",
    ),
    "presentation": (".ppt", ".pptx", ".pptm", ".pps", ".ppsx", ".potx", ".odp", ".otp", ".fodp", ".key"),
    "spreadsheet": (".xls", ".xlsx", ".xlsm", ".xlsb", ".xltx", ".ods", ".ots", ".fods", ".numbers"),
    "drawing": (".odg", ".otg", ".fodg"),
}
OFFICE_SUFFIXES = tuple(s for suffixes in OFFICE_TYPES.values() for s in suffixes)
OFFICE_CLASSES = {"document": "Word", "presentation": "Slides", "spreadsheet": "Spreadsheet", "drawing": "Slides"}
ODF_MIMETYPES = {
    b"application/vnd.oasis.opendocument.text": "document",
    b"application/vnd.oasis.opendocument.presentation": "presentation",
    b"application/vnd.oasis.opendocument.spreadsheet": "spreadsheet",
    b"application/vnd.oasis.opendocument.graphics": "drawing",
}
OOXML_PARTS = (("word/document.xml", "document"), ("ppt/presentation.xml", "presentation"), ("xl/workbook.xml", "spreadsheet"))

IMAGE_SUFFIXES = (
    ".apng", ".avif", ".avifs", ".blp", ".bmp", ".bw", ".cur", ".dcx", ".dds", ".dib", ".fit",
    ".fits", ".flc", ".fli", ".ftc", ".ftu", ".gbr", ".gif", ".icb", ".icns", ".ico", ".im",
    ".j2c", ".j2k", ".jfif", ".jp2", ".jpc", ".jpe", ".jpeg", ".jpf", ".jpg", ".jpx", ".msp",
    ".pbm", ".pcd", ".pcx", ".pfm", ".pgm", ".png", ".pnm", ".ppm", ".psd", ".pxr", ".qoi",
    ".ras", ".rgb", ".rgba", ".sgi", ".svg", ".svgz", ".tga", ".tif", ".tiff", ".vda", ".vst",
    ".wbmp", ".webp", ".xbm", ".xpm"
)

IMAGE_SIGNATURES = (
    (0, b"\x89PNG\r\n\x1a\n"), (0, b"\xff\xd8\xff"), (0, b"GIF87a"), (0, b"GIF89a"),
    (0, b"BM"), (0, b"II*\0"), (0, b"MM\0*"), (0, b"8BPS"), (0, b"qoif"), (0, b"DDS "),
    (0, b"icns"), (0, b"\0\0\0\x0cjP  \r\n\x87\n"), (0, b"\xffO\xffQ"), (4, b"ftypavif"),
    (4, b"ftypavis"), (8, b"WEBP")
)

PROSE_SUFFIXES = (
    ".txt", ".text", ".log", ".out", ".err", ".nfo", ".rst", ".adoc", ".asciidoc", ".org", ".tex",
    ".ltx", ".sty", ".cls", ".bib", ".bst", ".srt", ".vtt", ".sub", ".psv", ".json",
    ".jsonl", ".ndjson", ".json5", ".geojson", ".xml", ".xsd", ".xsl", ".xslt", ".rss",
    ".atom", ".plist", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".config", ".properties",
    ".env", ".editorconfig", ".gitignore", ".gitattributes", ".gitmodules", ".dockerignore",
    ".npmrc", ".lock", ".man", ".1", ".2", ".3", ".4", ".5", ".6", ".7", ".8", ".po", ".pot",
    ".strings", ".desktop", ".service", ".socket", ".timer", ".reg", ".pem", ".crt", ".csr", ".pub",
    ".asc", ".ics", ".vcf", ".eml", ".mbox", ".rmd", ".pod", ".kicad_sch", ".gcode"
)

CODE_SUFFIXES = (
    ".diff", ".patch", ".py", ".pyi", ".pyw", ".pyx", ".pxd", ".ipynb", ".c", ".h", ".cc", ".cpp",
    ".cxx", ".c++", ".hh", ".hpp", ".hxx", ".h++", ".inl", ".ipp", ".tpp", ".m", ".mm", ".cs",
    ".csx", ".java", ".kt", ".kts", ".scala", ".sc", ".groovy", ".gradle", ".clj", ".cljs", ".cljc",
    ".edn", ".go", ".rs", ".swift", ".dart", ".zig", ".nim", ".v", ".sv", ".svh", ".vhd", ".vhdl",
    ".d", ".pas", ".pp", ".f", ".f90", ".f95", ".f03", ".for", ".ada", ".adb", ".ads", ".js",
    ".mjs", ".cjs", ".jsx", ".ts", ".mts", ".cts", ".tsx", ".vue", ".svelte", ".astro", ".css",
    ".scss", ".sass", ".less", ".styl", ".php", ".phtml", ".rb", ".erb", ".rake", ".gemspec", ".pl",
    ".pm", ".t", ".lua", ".r", ".jl", ".hs", ".lhs", ".elm", ".ml", ".mli", ".fs", ".fsi", ".fsx",
    ".ex", ".exs", ".erl", ".hrl", ".lisp", ".lsp", ".cl", ".el", ".scm", ".ss", ".rkt", ".pro",
    ".prolog", ".tcl", ".awk", ".sed", ".vim", ".sh", ".bash", ".zsh", ".fish", ".ksh", ".csh",
    ".ps1", ".psm1", ".psd1", ".bat", ".cmd", ".mk", ".mak", ".make", ".cmake", ".ninja", ".bzl",
    ".bazel", ".nix", ".dockerfile", ".tf", ".tfvars", ".hcl", ".sql", ".psql", ".graphql", ".gql",
    ".proto", ".thrift", ".avsc", ".asm", ".s", ".nasm", ".ll", ".wat", ".glsl", ".vert", ".frag",
    ".hlsl", ".wgsl", ".cu", ".cuh", ".metal", ".qml", ".ui", ".qrc", ".pri", ".pro.user", ".sln",
    ".csproj", ".vcxproj", ".props", ".targets", ".xaml", ".rc", ".def", ".spec", ".http", ".rest",
    ".j2", ".jinja", ".jinja2", ".mustache", ".hbs", ".handlebars", ".liquid", ".twig", ".pug",
    ".haml", ".slim", ".tmpl", ".tpl", ".gd", ".gdshader", ".shader", ".cginc", ".usf", ".ush",
    ".sol", ".move", ".cairo", ".vy", ".ino", ".pde", ".scad"
)

TEXT_SUFFIXES = PROSE_SUFFIXES + CODE_SUFFIXES

CODE_NAMES = (
    "makefile", "gnumakefile", "dockerfile", "containerfile", "vagrantfile", "gemfile", "rakefile",
    "procfile", "justfile", "brewfile", "podfile", "cmakelists.txt", "pkgbuild", "meson.build",
    "build", "workspace", "jenkinsfile", "caddyfile"
)

PROSE_NAMES = (
    "license", "licence", "copying", "readme", "authors", "contributors", "changelog", "changes",
    "news", "notice", "todo", "install", "version", "codeowners"
)

TEXT_NAMES = PROSE_NAMES + CODE_NAMES


def has_text_name(path: str) -> bool:
    """True for the text and source files TEXT_SUFFIXES and TEXT_NAMES list."""
    name = os.path.basename(path).lower()
    return name in TEXT_NAMES or name.endswith(TEXT_SUFFIXES)


def has_image_name(path: str) -> bool:
    return path.lower().endswith(IMAGE_SUFFIXES)


def has_table_name(path: str) -> bool:
    return path.lower().endswith(TABLE_SUFFIXES)


def has_office_name(path: str) -> bool:
    return path.lower().endswith(OFFICE_SUFFIXES)


def _office_type_by_name(path: str):
    lower = path.lower()
    return next((kind for kind, suffixes in OFFICE_TYPES.items() if lower.endswith(suffixes)), None)


def _office_type_by_content(path: str, sample: bytes):
    if sample.startswith(b"{\\rtf"):
        return "document"
    if not sample.startswith(b"PK\x03\x04"):
        return None
    if sample[30:38] == b"mimetype":
        for mimetype, kind in ODF_MIMETYPES.items():
            if sample[38:38 + len(mimetype)] == mimetype:
                return kind
    import zipfile

    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
    except (zipfile.BadZipFile, OSError, ValueError):
        return None
    if "[Content_Types].xml" not in names:
        return None
    return next((kind for part, kind in OOXML_PARTS if part in names), None)


def office_type(path: str):
    """document, presentation, spreadsheet or drawing for an Office file, else None."""
    kind = _office_type_by_name(path)
    if kind is not None:
        return kind
    try:
        with open(path, "rb") as f:
            sample = f.read(SNIFF_BYTES)
    except OSError:
        return None
    return _office_type_by_content(path, sample)


def is_reader_name(path: str) -> bool:
    """True when the name alone says a document tab shows this file (PDF, Markdown, listed text, a table or an image)."""
    lower = path.lower()
    return (
        lower.endswith(".pdf")
        or lower.endswith(MARKDOWN_SUFFIXES)
        or has_text_name(path)
        or has_table_name(path)
        or lower.endswith(EBOOK_SUFFIXES)
        or has_office_name(path)
        or has_image_name(path)
    )


def has_image_signature(sample: bytes) -> bool:
    return any(sample[offset:offset + len(magic)] == magic for offset, magic in IMAGE_SIGNATURES)


def looks_like_text(sample: bytes) -> bool:
    if b"\0" in sample:
        return False
    try:
        sample.decode("utf-8")
    except UnicodeDecodeError as error:
        return error.reason == "unexpected end of data"
    return True


def document_kind(path: str) -> str:
    lower = path.lower()
    if lower.endswith(".pdf"):
        return "pdf"
    if lower.endswith(MARKDOWN_SUFFIXES):
        return "markdown"
    if lower.endswith(HTML_SUFFIXES):
        return "html"
    if lower.endswith(TABLE_SUFFIXES):
        return "table"
    if lower.endswith(EBOOK_SUFFIXES):
        return "ebook"
    if lower.endswith(OFFICE_SUFFIXES):
        return "office"
    if lower.endswith(IMAGE_SUFFIXES):
        return "image"
    try:
        with open(path, "rb") as f:
            sample = f.read(SNIFF_BYTES)
    except OSError:
        return "text" if has_text_name(path) else "binary"
    if sample.startswith(b"%PDF-"):
        return "pdf"
    if sample.startswith(b"{\\rtf"):
        return "office"
    if looks_like_text(sample):
        return "text"
    if sample.startswith(b"PK\x03\x04") and sample[30:30 + len(EPUB_MIMETYPE)] == EPUB_MIMETYPE:
        return "ebook"
    if _office_type_by_content(path, sample):
        return "office"
    return "image" if has_image_signature(sample) else "binary"


def is_text_document(path: str) -> bool:
    """True for files the reader shows as one HTML page (Markdown, plain text, a table as text, an e-book) rather than as a PDF."""
    return document_kind(path) in ("markdown", "text", "table", "ebook")


def document_class(path: str) -> str:
    name = os.path.basename(path).lower()
    if name in CODE_NAMES or name.endswith(CODE_SUFFIXES):
        return "Code"
    if has_table_name(path):
        return "Spreadsheet"
    if path.lower().endswith(EBOOK_SUFFIXES):
        return "E-book"
    office = _office_type_by_name(path)
    if office:
        return OFFICE_CLASSES[office]
    if has_text_name(path):
        return "Text"
    if has_image_name(path):
        return "Image"
    kind = document_kind(path)
    if kind == "office":
        return OFFICE_CLASSES.get(office_type(path) or "document", "Word")
    return {"pdf": "PDF", "markdown": "Markdown", "image": "Image", "ebook": "E-book"}.get(kind, "Text")


def _patterns(suffixes, upper: bool = False) -> str:
    return " ".join(f"*{s} *{s.upper()}" if upper else f"*{s}" for s in suffixes)


def dialog_filter(html: bool = True) -> str:
    """
    The open dialogs' filter: every supported file (HTML only when html), then
    PDFs, images and everything. Image and PDF names are listed in capitals
    too, since cameras write IMG_0001.JPG and the Linux dialog matches case.
    """
    suffixes = MARKDOWN_SUFFIXES + (HTML_SUFFIXES if html else ()) + EBOOK_SUFFIXES + TABLE_SUFFIXES + TEXT_SUFFIXES
    images = _patterns(IMAGE_SUFFIXES, upper=True)
    office = _patterns(OFFICE_SUFFIXES, upper=True)
    supported = f"*.pdf *.PDF {office} {_patterns(suffixes)} {images} {' '.join(TEXT_NAMES)}"
    return ";;".join(
        (
            f"Supported Files ({supported})",
            "PDF Files (*.pdf *.PDF)",
            f"Office Files ({office})",
            f"Images ({images})",
            "All Files (*)",
        )
    )
