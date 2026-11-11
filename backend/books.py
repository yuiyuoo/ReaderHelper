"""Read title, author, language and the embedded cover image from an EPUB file."""
import logging
import posixpath
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

log = logging.getLogger(__name__)

NS = {
    "c": "urn:oasis:names:tc:opendocument:xmlns:container",
    "opf": "http://www.idpf.org/2007/opf",
    "dc": "http://purl.org/dc/elements/1.1/",
}
IMAGE_TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/gif": "gif", "image/webp": "webp"}
MAX_COVER_BYTES = 5 * 1024 * 1024


def _text(el) -> str:
    return re.sub(r"\s+", " ", (el.text or "")).strip() if el is not None else ""


def read_epub_meta(path: Path) -> dict:
    """Return {title, author, language, cover: (bytes, ext) | None}. Never raises; missing parts are empty."""
    meta = {"title": "", "author": "", "language": "", "cover": None}
    try:
        with zipfile.ZipFile(path) as z:
            container = ET.fromstring(z.read("META-INF/container.xml"))
            rootfile = container.find(".//c:rootfile", NS)
            opf_path = rootfile.get("full-path")
            opf = ET.fromstring(z.read(opf_path))
            md = opf.find("opf:metadata", NS)
            if md is not None:
                meta["title"] = _text(md.find("dc:title", NS))
                meta["author"] = _text(md.find("dc:creator", NS))
                meta["language"] = _text(md.find("dc:language", NS))[:2].lower()
            meta["cover"] = _find_cover(z, opf, opf_path)
    except (KeyError, zipfile.BadZipFile, ET.ParseError, AttributeError, OSError) as e:
        log.warning("could not read EPUB metadata from %s: %s", path.name, e)
    return meta


def _find_cover(z: zipfile.ZipFile, opf, opf_path: str):
    """EPUB 3: manifest item with properties="cover-image". EPUB 2: <meta name="cover" content="<id>">.
    Fallback: the first image whose id or file name contains "cover"."""
    items = opf.findall("opf:manifest/opf:item", NS)
    images = [i for i in items if i.get("media-type") in IMAGE_TYPES]
    item = next((i for i in images if "cover-image" in (i.get("properties") or "").split()), None)
    if item is None:
        meta = opf.find("opf:metadata/opf:meta[@name='cover']", NS)
        cover_id = meta.get("content") if meta is not None else None
        item = next((i for i in images if i.get("id") == cover_id), None)
    if item is None:
        item = next((i for i in images if "cover" in (i.get("id", "") + i.get("href", "")).lower()), None)
    if item is None:
        return None
    # hrefs are relative to the OPF file and may be URL-encoded.
    href = posixpath.normpath(posixpath.join(posixpath.dirname(opf_path), item.get("href", "")))
    try:
        data = z.read(href.replace("%20", " "))
    except KeyError:
        return None
    if len(data) > MAX_COVER_BYTES:
        return None
    return data, IMAGE_TYPES[item.get("media-type")]


def sniff_image(data: bytes) -> str | None:
    """File extension for an uploaded cover, decided by its content (not the file name)."""
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None
