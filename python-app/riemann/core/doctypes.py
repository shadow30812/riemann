"""
What kind of document a file is, decided in one place for the whole app.

document_kind(path) returns "pdf", "markdown", "html", "text" or "binary":

- a name ending in .pdf, or a file starting with %PDF-, is a PDF;
- .md and .markdown are Markdown; .html, .htm and .xhtml are HTML, which opens
  in the browser;
- any other file is text when its first SNIFF_BYTES hold no NUL byte and decode
  as UTF-8 (a character cut off at the end of the sample is allowed), so .py,
  .cpp, .log, Makefile and files with no extension all count. Everything else
  is binary, and the app refuses to open it.

The reader shows PDFs, Markdown and text. TEXT_SUFFIXES and TEXT_NAMES (the
prose and code lists together) name the common text and source-code files.
They are used where reading the file is too slow or not trusted: the explorer's icons, the open dialogs'
filter, and window/instance.py, which decides what a running Riemann opens
when another process hands it a path (a security boundary, so it never
sniffs content).

document_class(path) sorts the Documents history into DOCUMENT_CLASSES (PDF,
Markdown, Code, Text): CODE_SUFFIXES and CODE_NAMES are source, build and
style files; PROSE_SUFFIXES and PROSE_NAMES are prose, data, configuration
and logs, which count as Text. It goes by name first, since history lists
files that may be gone, and reads only a file whose name says nothing.
"""

import os

SNIFF_BYTES = 8192
MARKDOWN_SUFFIXES = (".md", ".markdown")
HTML_SUFFIXES = (".html", ".htm", ".xhtml")
DOCUMENT_CLASSES = ("PDF", "Markdown", "Code", "Text")

PROSE_SUFFIXES = (
    ".txt", ".text", ".log", ".out", ".err", ".nfo", ".rst", ".adoc", ".asciidoc", ".org", ".tex",
    ".ltx", ".sty", ".cls", ".bib", ".bst", ".srt", ".vtt", ".sub", ".csv", ".tsv", ".psv", ".json",
    ".jsonl", ".ndjson", ".json5", ".geojson", ".xml", ".xsd", ".xsl", ".xslt", ".svg", ".rss",
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


def is_reader_name(path: str) -> bool:
    """True when the name alone says the reader shows this file (PDF, Markdown or listed text)."""
    lower = path.lower()
    return lower.endswith(".pdf") or lower.endswith(MARKDOWN_SUFFIXES) or has_text_name(path)


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
    try:
        with open(path, "rb") as f:
            sample = f.read(SNIFF_BYTES)
    except OSError:
        return "text" if has_text_name(path) else "binary"
    if sample.startswith(b"%PDF-"):
        return "pdf"
    return "text" if looks_like_text(sample) else "binary"


def is_text_document(path: str) -> bool:
    """True for files the reader shows as a page of text (Markdown or plain text) rather than as a PDF."""
    return document_kind(path) in ("markdown", "text")


def document_class(path: str) -> str:
    name = os.path.basename(path).lower()
    if name in CODE_NAMES or name.endswith(CODE_SUFFIXES):
        return "Code"
    if has_text_name(path):
        return "Text"
    return {"pdf": "PDF", "markdown": "Markdown"}.get(document_kind(path), "Text")


def dialog_filter(html: bool = True) -> str:
    """The open dialogs' filter: every PDF, Markdown and listed text file (and HTML when html), then everything."""
    suffixes = MARKDOWN_SUFFIXES + (HTML_SUFFIXES if html else ()) + TEXT_SUFFIXES
    patterns = ["*.pdf", "*.PDF"] + [f"*{s}" for s in suffixes] + list(TEXT_NAMES)
    return f"Supported Files ({' '.join(patterns)});;All Files (*)"
