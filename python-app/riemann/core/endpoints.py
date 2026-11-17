"""
Addresses of the services Riemann talks to, in one place.

LOOPBACK is the only address Riemann's own servers (the AI engine, the captions
server) listen on and the app connects to; keep every local service on it (see
the Security rules in AGENTS.md). loopback_url builds a request URL for one.

The remote ones are the scholarly metadata APIs, the link formats written into
citations and exports, the model host, the release downloads (the Snip-to-LaTeX
module pack), the CDN KaTeX comes from, the AI providers of the assistant
panel, and LibreOffice's download page and the Flathub repository it can be
installed from (core/office_install.py). Whatever is fetched from these is pinned by version and hash where it is
used. The web search engine and the Ollama address are user settings
(core.settings.search_url, ui.sidecar.ollama_url); only their defaults are here.

USER_AGENT identifies Riemann to the metadata APIs, which ask callers to.

No Qt imports: the AI engine's child process uses this module.
"""

LOOPBACK = "127.0.0.1"

USER_AGENT = "Riemann PDF reader (https://github.com/shadow30812/riemann)"

CROSSREF_WORKS = "https://api.crossref.org/works"
OPENALEX_WORKS = "https://api.openalex.org/works"
ARXIV_ABS = "https://arxiv.org/abs"
DOI_RESOLVER = "https://doi.org"
HUGGINGFACE = "https://huggingface.co"
RELEASES = "https://github.com/shadow30812/riemann/releases/download"
JSDELIVR_NPM = "https://cdn.jsdelivr.net/npm"

ANTHROPIC_API = "https://api.anthropic.com/v1"
OPENAI_API = "https://api.openai.com/v1"
OPENROUTER_API = "https://openrouter.ai/api/v1"
GEMINI_API = "https://generativelanguage.googleapis.com/v1beta"
OLLAMA_DEFAULT_URL = "http://localhost:11434"

DEFAULT_SEARCH_URL = "https://www.google.com/search?q={query}"

LIBREOFFICE_DOWNLOAD = "https://www.libreoffice.org/download/download-libreoffice/"
FLATHUB_REPO = "https://dl.flathub.org/repo/flathub.flatpakrepo"


def loopback_url(port: int, path: str = "") -> str:
    return f"http://{LOOPBACK}:{port}/{path.lstrip('/')}"


def arxiv_url(arxiv_id: str) -> str:
    return f"{ARXIV_ABS}/{arxiv_id}"


def doi_url(doi: str) -> str:
    return f"{DOI_RESOLVER}/{doi}"
