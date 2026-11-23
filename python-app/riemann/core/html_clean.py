"""
The HTML cleaner: every route that turns a document (EPUB, and the basic
views of Word, PowerPoint and OpenDocument files) into HTML for the reader's
web view passes its output through clean_html.

The reader's web view runs JavaScript (KaTeX typesets maths in it) and a
document may come from anywhere, so nothing from the document may run code,
reach the network or cover the page:

- Only the elements in BLOCK, INLINE, TABLE, MEDIA and MATHML are kept, each
  with only the attributes listed for it in ATTRIBUTES (and GLOBAL_ATTRIBUTES).
  Event handlers, style attributes and classes never are.
- DROPPED elements go with everything inside them: scripts, styles, frames,
  objects, forms and their controls, templates, head and its meta and link
  elements. Any other element is unwrapped: its text and children stay.
- href survives only as http, https or mailto, or as an in-page #anchor;
  anything else (javascript:, file:, a relative path) leaves the text without
  the link. url_is_safe from ui/reader/utils.py is not used here because it
  allows relative links.
- An img keeps its src only as an embedded data:image/... URI (PNG, JPEG,
  GIF, WebP, BMP or SVG, which an img never runs scripts from), or when
  resolve_image (given by the caller, which knows the document's own files)
  turns the src into one. Remote pictures are dropped, so opening a document
  never tells its sender that it was opened.
- An SVG wrapper around one picture (how EPUB covers are often written)
  becomes a plain img; any other inline SVG is dropped.
- Comments and processing instructions are removed.

The result is the inner HTML of the body, ready to put into the reader's own
page template.
"""

import html as html_lib
import re
from collections.abc import Callable

from lxml import etree
from lxml import html as lxml_html

BLOCK = {
    "p", "div", "section", "article", "header", "footer", "aside", "nav", "main", "blockquote", "pre",
    "h1", "h2", "h3", "h4", "h5", "h6", "hr", "ul", "ol", "li", "dl", "dt", "dd", "figure",
    "figcaption", "details", "summary", "address", "center",
}
INLINE = {
    "a", "abbr", "b", "bdi", "bdo", "br", "cite", "code", "data", "del", "dfn", "em", "i", "ins",
    "kbd", "mark", "q", "rp", "rt", "ruby", "s", "samp", "small", "span", "strike", "strong", "sub",
    "sup", "time", "tt", "u", "var", "wbr",
}
TABLE = {"table", "caption", "colgroup", "col", "thead", "tbody", "tfoot", "tr", "th", "td"}
MEDIA = {"img"}
MATHML = {
    "math", "mi", "mn", "mo", "ms", "mtext", "mspace", "mrow", "mfrac", "msqrt", "mroot", "mstyle",
    "merror", "mpadded", "mphantom", "menclose", "msub", "msup", "msubsup", "munder", "mover",
    "munderover", "mmultiscripts", "mprescripts", "none", "mtable", "mtr", "mtd", "semantics",
}
ALLOWED = BLOCK | INLINE | TABLE | MEDIA | MATHML
DROPPED = {
    "script", "style", "iframe", "frame", "frameset", "object", "embed", "applet", "param", "form",
    "input", "button", "select", "option", "optgroup", "textarea", "label", "fieldset", "legend",
    "output", "template", "noscript", "noembed", "noframes", "head", "title", "meta", "link", "base",
    "audio", "video", "source", "track", "canvas", "map", "area", "dialog", "portal", "slot",
    "annotation", "annotation-xml", "svg",
}
GLOBAL_ATTRIBUTES = {"id", "title", "lang", "dir"}
ATTRIBUTES = {
    "a": {"href"},
    "img": {"src", "alt", "width", "height"},
    "ol": {"start", "type", "reversed"},
    "li": {"value"},
    "td": {"colspan", "rowspan", "headers"},
    "th": {"colspan", "rowspan", "headers", "scope", "abbr"},
    "col": {"span"},
    "colgroup": {"span"},
    "time": {"datetime"},
    "data": {"value"},
    "math": {"display"},
    "mfrac": {"linethickness"},
    "mo": {"stretchy", "fence", "separator", "lspace", "rspace"},
    "mtd": {"columnspan", "rowspan"},
    "details": {"open"},
}
LINK_SCHEMES = ("http", "https", "mailto")
DATA_IMAGE = re.compile(r"^data:image/(png|jpe?g|gif|webp|bmp|svg\+xml);base64,[A-Za-z0-9+/=\s]+$", re.I)
SCHEME = re.compile(r"^([a-z][a-z0-9+.\-]*):", re.I)
IGNORED_IN_URL = re.compile(r"[\x00-\x20\x7f]+")
NUMBER = re.compile(r"^\d{1,5}(%|px)?$")

ImageResolver = Callable[[str], str | None]
LinkRewriter = Callable[[str], str | None]


def _local_name(tag) -> str:
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1].rsplit(":", 1)[-1].lower()


def safe_href(href: str) -> str | None:
    text = IGNORED_IN_URL.sub("", href or "")
    if text.startswith("#") and len(text) > 1:
        return text
    match = SCHEME.match(text)
    if match and match.group(1).lower() in LINK_SCHEMES:
        return text
    return None


def safe_image(src: str, resolve_image: ImageResolver | None) -> str | None:
    src = (src or "").strip()
    if DATA_IMAGE.match(src):
        return src
    if resolve_image is not None and src and not SCHEME.match(src):
        resolved = resolve_image(src)
        if resolved and DATA_IMAGE.match(resolved):
            return resolved
    return None


def _svg_picture(element) -> str | None:
    for child in element.iter():
        if _local_name(child.tag) == "image":
            for name, value in child.attrib.items():
                if _local_name(name) == "href":
                    return value
    return None


def _unwrap(element) -> None:
    parent = element.getparent()
    if parent is None:
        return
    index = parent.index(element)
    previous = element.getprevious()
    if element.text:
        if previous is not None:
            previous.tail = (previous.tail or "") + element.text
        else:
            parent.text = (parent.text or "") + element.text
    children = list(element)
    for offset, child in enumerate(children):
        parent.insert(index + offset, child)
    last = children[-1] if children else element.getprevious()
    if element.tail:
        if last is not None and last is not element:
            last.tail = (last.tail or "") + element.tail
        else:
            parent.text = (parent.text or "") + element.tail
    parent.remove(element)


def _remove(element) -> None:
    parent = element.getparent()
    if parent is None:
        return
    if element.tail:
        previous = element.getprevious()
        if previous is not None:
            previous.tail = (previous.tail or "") + element.tail
        else:
            parent.text = (parent.text or "") + element.tail
    parent.remove(element)


def _clean_attributes(element, name: str, options: dict) -> bool:
    """Keeps the allowed attributes; returns False when the element should be unwrapped (a link with no safe target)."""
    resolve_image, rewrite_link, id_prefix = options["resolve_image"], options["rewrite_link"], options["id_prefix"]
    allowed = GLOBAL_ATTRIBUTES | ATTRIBUTES.get(name, set())
    values = {_local_name(k): v for k, v in element.attrib.items()}
    element.attrib.clear()
    for key, value in values.items():
        if key not in allowed:
            continue
        if key == "href":
            if rewrite_link is not None:
                value = rewrite_link(value)
            elif value.strip().startswith("#"):
                value = "#" + id_prefix + value.strip()[1:]
            value = safe_href(value) if value else None
        elif key == "id":
            value = id_prefix + value
        elif key == "src":
            value = safe_image(value, resolve_image)
        elif key in ("width", "height") and not NUMBER.match(value.strip()):
            value = None
        if value is not None:
            element.set(key, value)
    if name == "a":
        return "href" in element.attrib or "id" in element.attrib
    if name == "img":
        return "src" in element.attrib
    return True


def _walk(root, options: dict) -> None:
    resolve_image = options["resolve_image"]
    for element in list(root.iter()):
        if element is root or element.getparent() is None:
            continue
        if not isinstance(element.tag, str):
            _remove(element)
            continue
        name = _local_name(element.tag)
        if name == "svg":
            picture = _svg_picture(element)
            src = safe_image(picture, resolve_image) if picture else None
            if src:
                img = etree.Element("img", src=src, alt="")
                img.tail = element.tail
                element.getparent().replace(element, img)
            else:
                _remove(element)
            continue
        if name in DROPPED:
            _remove(element)
            continue
        if name not in ALLOWED:
            _unwrap(element)
            continue
        element.tag = name
        if not _clean_attributes(element, name, options):
            if name == "img":
                _remove(element)
            else:
                _unwrap(element)


def _drop_namespaces(root) -> None:
    for element in root.iter():
        if not isinstance(element.tag, str):
            continue
        attributes = {_local_name(k): v for k, v in element.attrib.items()}
        element.attrib.clear()
        element.tag = _local_name(element.tag)
        for key, value in attributes.items():
            element.set(key, value)
    etree.cleanup_namespaces(root)


def _parse(markup: str, xml: bool):
    if xml:
        parser = etree.XMLParser(
            recover=True, resolve_entities=False, no_network=True, remove_comments=True, remove_pis=True
        )
        try:
            root = etree.fromstring(markup if isinstance(markup, bytes) else markup.encode("utf-8"), parser)
        except etree.XMLSyntaxError:
            root = None
        if root is not None:
            _drop_namespaces(root)
            return next((e for e in root.iter() if e.tag == "body"), root)
    if isinstance(markup, bytes):
        markup = markup.decode("utf-8", errors="replace")
    document = lxml_html.document_fromstring(markup)
    body = document.find("body")
    return body if body is not None else document


def clean_html(
    markup,
    resolve_image: ImageResolver | None = None,
    xml: bool = False,
    rewrite_link: LinkRewriter | None = None,
    id_prefix: str = "",
) -> str:
    """
    The safe inner HTML of markup's body (see the module docstring). markup
    is text, or bytes for xml. xml parses it as XHTML, as an EPUB chapter is
    written, so an empty <title/> doesn't swallow the page the way the HTML
    parser would let it. rewrite_link turns a link into another before it is
    checked (it returns the final link, or None to drop it); without one, an
    in-page #anchor gets id_prefix, as every id does, so pages joined into one
    keep their anchors apart.
    """
    if not markup or not markup.strip():
        return ""
    body = _parse(markup, xml)
    options = {"resolve_image": resolve_image, "rewrite_link": rewrite_link, "id_prefix": id_prefix}
    _walk(body, options)
    parts = [html_lib.escape(body.text) if body.text else ""]
    for child in body:
        parts.append(lxml_html.tostring(child, encoding="unicode", method="html"))
    return "".join(parts)
