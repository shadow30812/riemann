"""
EPUB books as one HTML page for the reader's text view.

read_epub(path) returns {"title", "html", "sections", "note"}:

- The chapters are the spine's XHTML items, in reading order, each cleaned by
  core.html_clean (as XHTML) and wrapped in <section id="cN">. Every id in
  chapter N becomes "cN-<id>", and links between chapters ("ch2.xhtml#fig3")
  become in-page anchors ("#c2-fig3"), so the book reads as one page and its
  own links still work. Links to anything outside the book that isn't http,
  https or mailto are dropped by the cleaner.
- Pictures are read from the book and embedded as data URIs, up to
  IMAGE_LIMIT bytes each and IMAGES_TOTAL in all; past that the text stays and
  the note says pictures were left out. Pictures on the web are never fetched.
- The book's stylesheets and scripts are dropped, so the reader's page theme
  (the N key) applies.
- sections is the book's table of contents as (depth, title, anchor): from
  the EPUB 3 navigation document, or the EPUB 2 NCX file, or, failing both,
  one entry per chapter named by its first heading.
- The title and author from the package metadata head the page.

Zip entries are read only up to their declared size and the whole book only
up to BOOK_LIMIT bytes uncompressed, so a zip bomb stops early. XML is
parsed without resolving entities or reaching the network.

It imports no Qt: it runs in the converter child process
(core/convert_process.py).
"""

import base64
import html
import posixpath
import zipfile
from typing import Optional
from urllib.parse import unquote, urldefrag

from lxml import etree

from .html_clean import clean_html

CONTAINER = "META-INF/container.xml"
CHAPTER_TYPES = ("application/xhtml+xml", "text/html", "application/x-dtbook+xml")
IMAGE_TYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
    ".webp": "image/webp", ".svg": "image/svg+xml", ".bmp": "image/bmp",
}
IMAGE_LIMIT = 15 * 1024 * 1024
IMAGES_TOTAL = 150 * 1024 * 1024
CHAPTER_LIMIT = 32 * 1024 * 1024
BOOK_LIMIT = 512 * 1024 * 1024
HEADINGS = ("h1", "h2", "h3")


class EpubError(Exception):
    pass


def _parser() -> etree.XMLParser:
    return etree.XMLParser(recover=True, resolve_entities=False, no_network=True, remove_comments=True)


def _local(tag) -> str:
    return tag.rsplit("}", 1)[-1].lower() if isinstance(tag, str) else ""


def _attr(element, name: str) -> Optional[str]:
    for key, value in element.attrib.items():
        if _local(key) == name:
            return value
    return None


class _Book:
    def __init__(self, archive: zipfile.ZipFile):
        self.archive = archive
        self.read_bytes = 0
        self.image_bytes = 0
        self.images_left_out = False

    def read(self, name: str, limit: int) -> bytes:
        info = self.archive.getinfo(name)
        if info.file_size > limit or self.read_bytes + info.file_size > BOOK_LIMIT:
            raise EpubError(f"{name} is too large")
        with self.archive.open(info) as handle:
            data = handle.read(info.file_size + 1)
        if len(data) > info.file_size:
            raise EpubError(f"{name} is larger than the book says")
        self.read_bytes += len(data)
        return data

    def xml(self, name: str):
        root = etree.fromstring(self.read(name, CHAPTER_LIMIT), _parser())
        if root is None:
            raise EpubError(f"{name} can't be read")
        return root

    def image(self, name: str) -> Optional[str]:
        media = IMAGE_TYPES.get(posixpath.splitext(name)[1].lower())
        if media is None:
            return None
        try:
            info = self.archive.getinfo(name)
        except KeyError:
            return None
        if info.file_size > IMAGE_LIMIT or self.image_bytes + info.file_size > IMAGES_TOTAL:
            self.images_left_out = True
            return None
        try:
            data = self.read(name, IMAGE_LIMIT)
        except EpubError:
            self.images_left_out = True
            return None
        self.image_bytes += len(data)
        return f"data:{media};base64,{base64.b64encode(data).decode('ascii')}"


def _resolve(base: str, href: str) -> str:
    return posixpath.normpath(posixpath.join(posixpath.dirname(base), unquote(href))).lstrip("/")


def _package(book: _Book):
    try:
        container = book.xml(CONTAINER)
    except KeyError:
        raise EpubError("This isn't an EPUB book: it has no META-INF/container.xml.")
    rootfile = next((e for e in container.iter() if _local(e.tag) == "rootfile"), None)
    path = _attr(rootfile, "full-path") if rootfile is not None else None
    if not path:
        raise EpubError("The book doesn't say where its contents are.")
    return path, book.xml(path)


def _metadata(opf) -> tuple:
    title = author = ""
    for element in opf.iter():
        name = _local(element.tag)
        if name == "title" and not title:
            title = (element.text or "").strip()
        elif name == "creator" and not author:
            author = (element.text or "").strip()
    return title, author


def _manifest(opf, opf_path: str) -> dict:
    items = {}
    for element in opf.iter():
        if _local(element.tag) == "item" and element.get("id") and element.get("href"):
            items[element.get("id")] = {
                "path": _resolve(opf_path, element.get("href")),
                "type": (element.get("media-type") or "").lower(),
                "properties": (element.get("properties") or "").split(),
            }
    return items


def _spine(opf, manifest: dict) -> tuple:
    chapters, toc_id = [], None
    for element in opf.iter():
        name = _local(element.tag)
        if name == "spine":
            toc_id = element.get("toc")
        elif name == "itemref":
            item = manifest.get(element.get("idref"))
            if item and item["type"] in CHAPTER_TYPES and item["path"] not in chapters:
                chapters.append(item["path"])
    return chapters, toc_id


def _anchor(chapters: dict, base: str, href: str) -> Optional[str]:
    target, fragment = urldefrag(href)
    path = _resolve(base, target) if target else base
    if path not in chapters:
        return None
    index = chapters[path]
    return f"#c{index}-{fragment}" if fragment else f"#c{index}"


def _nav_sections(book: _Book, nav_path: str, chapters: dict) -> list:
    root = book.xml(nav_path)
    navs = [e for e in root.iter() if _local(e.tag) == "nav"]
    nav = next((n for n in navs if _attr(n, "type") == "toc"), navs[0] if navs else None)
    sections: list = []
    if nav is None:
        return sections

    def walk(element, depth: int) -> None:
        for item in element:
            if _local(item.tag) != "li":
                continue
            link = next((e for e in item if _local(e.tag) in ("a", "span")), None)
            if link is not None:
                title = " ".join("".join(link.itertext()).split())
                anchor = _anchor(chapters, nav_path, link.get("href") or "") if link.get("href") else None
                sections.append((depth, title or "Untitled", anchor or ""))
            for child in item:
                if _local(child.tag) == "ol":
                    walk(child, depth + 1)

    for child in nav:
        if _local(child.tag) == "ol":
            walk(child, 0)
    return sections


def _ncx_sections(book: _Book, ncx_path: str, chapters: dict) -> list:
    root = book.xml(ncx_path)
    sections: list = []

    def walk(element, depth: int) -> None:
        for point in element:
            if _local(point.tag) != "navpoint":
                continue
            label = next((e for e in point.iter() if _local(e.tag) == "text"), None)
            content = next((e for e in point if _local(e.tag) == "content"), None)
            title = " ".join((label.text or "").split()) if label is not None else ""
            anchor = _anchor(chapters, ncx_path, content.get("src") or "") if content is not None else None
            sections.append((depth, title or "Untitled", anchor or ""))
            walk(point, depth + 1)

    nav_map = next((e for e in root.iter() if _local(e.tag) == "navmap"), None)
    if nav_map is not None:
        walk(nav_map, 0)
    return sections


def _first_heading(markup: str) -> str:
    from lxml import html as lxml_html

    if not markup.strip():
        return ""
    fragment = lxml_html.fragment_fromstring(markup, create_parent="div")
    for element in fragment.iter(*HEADINGS):
        text = " ".join(element.text_content().split())
        if text:
            return text
    return ""


def read_epub(path: str) -> dict:
    try:
        archive = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as error:
        raise EpubError(f"The book can't be opened ({error}).")
    with archive:
        book = _Book(archive)
        opf_path, opf = _package(book)
        title, author = _metadata(opf)
        manifest = _manifest(opf, opf_path)
        spine, toc_id = _spine(opf, manifest)
        if not spine:
            raise EpubError("The book has no chapters to show.")
        chapters = {name: i for i, name in enumerate(spine)}
        parts, fallback = [], []
        if title:
            parts.append(f"<h1>{html.escape(title)}</h1>")
        if author:
            parts.append(f"<p><em>{html.escape(author)}</em></p>")
        for index, name in enumerate(spine):
            try:
                data = book.read(name, CHAPTER_LIMIT)
            except (KeyError, EpubError):
                continue
            body = clean_html(
                data,
                resolve_image=lambda src, base=name: book.image(_resolve(base, src)),
                xml=True,
                rewrite_link=lambda href, base=name: href if ":" in href.split("/")[0] else _anchor(chapters, base, href),
                id_prefix=f"c{index}-",
            )
            parts.append(f'<section id="c{index}">{body}</section>')
            fallback.append((0, _first_heading(body) or f"Part {index + 1}", f"#c{index}"))
        sections = []
        nav = next((i["path"] for i in manifest.values() if "nav" in i["properties"]), None)
        ncx = manifest.get(toc_id, {}).get("path") if toc_id else None
        ncx = ncx or next((i["path"] for i in manifest.values() if i["type"] == "application/x-dtbncx+xml"), None)
        for source, reader in ((nav, _nav_sections), (ncx, _ncx_sections)):
            if source and not sections:
                try:
                    sections = reader(book, source, chapters)
                except (KeyError, EpubError, etree.XMLSyntaxError):
                    sections = []
        note = "Some pictures were too large and were left out." if book.images_left_out else ""
    return {"title": title, "html": "\n".join(parts), "sections": sections or fallback, "note": note}
