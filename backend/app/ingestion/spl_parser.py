import io
import logging
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from lxml import etree

logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}


@dataclass
class ExtractedImage:
    filename: str
    media_id: str
    data: bytes
    section_title: str = ""
    xml_context: str = ""


@dataclass
class ParsedSPL:
    setid: str
    version_number: int = 0
    effective_time: str = ""
    product_name: str = ""
    generic_name: str = ""
    manufacturer: str = ""
    images: list[ExtractedImage] = field(default_factory=list)


def _strip_ns(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _first_text(elem: etree._Element | None) -> str:
    if elem is None:
        return ""
    return " ".join("".join(elem.itertext()).split())


def parse_spl_xml(xml_bytes: bytes, available_images: dict[str, bytes]) -> ParsedSPL | None:
    """Parses HL7 SPL XML bytes and maps observationMedia references to image files."""
    try:
        parser = etree.XMLParser(recover=True, huge_tree=True)
        root = etree.fromstring(xml_bytes, parser=parser)
    except Exception as exc:
        logger.warning("Failed to parse SPL XML: %s", exc)
        return None

    setid_el = root.find(".//{*}setId")
    setid = setid_el.get("root", "").strip() if setid_el is not None else ""
    if not setid:
        return None

    ver_el = root.find(".//{*}versionNumber")
    try:
        version_number = int(ver_el.get("value", "0")) if ver_el is not None else 0
    except ValueError:
        version_number = 0

    eff_el = root.find(".//{*}effectiveTime")
    effective_time = eff_el.get("value", "") if eff_el is not None else ""

    # Product & generic names
    prod_name_el = root.find(".//{*}manufacturedProduct//{*}name")
    product_name = _first_text(prod_name_el)

    gen_name_el = root.find(".//{*}asEntityWithGeneric//{*}genericMedicine/{*}name")
    generic_name = _first_text(gen_name_el)

    mfg_el = root.find(".//{*}author//{*}representedOrganization/{*}name")
    manufacturer = _first_text(mfg_el)

    # Map media_id -> image filename
    media_map: dict[str, str] = {}
    for obs in root.findall(".//{*}observationMedia"):
        media_id = obs.get("ID", "").strip()
        ref_el = obs.find(".//{*}value/{*}reference")
        ref_val = ref_el.get("value", "").strip() if ref_el is not None else ""
        if media_id and ref_val:
            media_map[media_id] = Path(ref_val).name.lower()

    # Walk sections to extract context around renderMultiMedia
    section_contexts: dict[str, tuple[str, str]] = {}
    for section in root.findall(".//{*}section"):
        sec_title = _first_text(section.find("./{*}title"))
        sec_text = _first_text(section.find("./{*}text"))[:600]
        for render in section.findall(".//{*}renderMultiMedia"):
            ref_obj = render.get("referencedObject", "").strip()
            if ref_obj and ref_obj not in section_contexts:
                section_contexts[ref_obj] = (sec_title, sec_text)

    # Build ExtractedImage entries for every image file present in the zip
    extracted: list[ExtractedImage] = []
    used_files: set[str] = set()

    for media_id, fname_lower in media_map.items():
        if fname_lower in available_images:
            sec_title, sec_text = section_contexts.get(media_id, ("", ""))
            extracted.append(
                ExtractedImage(
                    filename=fname_lower,
                    media_id=media_id,
                    data=available_images[fname_lower],
                    section_title=sec_title,
                    xml_context=sec_text,
                )
            )
            used_files.add(fname_lower)

    # Fallback: include any unreferenced image files in the inner zip
    for fname_lower, img_bytes in available_images.items():
        if fname_lower not in used_files:
            extracted.append(
                ExtractedImage(
                    filename=fname_lower,
                    media_id="",
                    data=img_bytes,
                    section_title="",
                    xml_context="",
                )
            )

    return ParsedSPL(
        setid=setid,
        version_number=version_number,
        effective_time=effective_time,
        product_name=product_name,
        generic_name=generic_name,
        manufacturer=manufacturer,
        images=extracted,
    )


def iter_spl_archive(outer_zip_path: Path) -> Iterator[ParsedSPL]:
    """
    Streams an outer DailyMed release ZIP, reading each inner <setid>.zip in memory
    to avoid extracting hundreds of gigabytes of raw files onto disk.
    """
    with zipfile.ZipFile(outer_zip_path, "r") as outer_zf:
        for info in outer_zf.infolist():
            if info.is_dir() or not info.filename.lower().endswith(".zip"):
                continue
            try:
                inner_bytes = outer_zf.read(info.filename)
                with zipfile.ZipFile(io.BytesIO(inner_bytes), "r") as inner_zf:
                    xml_bytes: bytes | None = None
                    images: dict[str, bytes] = {}
                    for inner_info in inner_zf.infolist():
                        if inner_info.is_dir():
                            continue
                        name_lower = Path(inner_info.filename).name.lower()
                        ext = Path(name_lower).suffix
                        if ext == ".xml" and xml_bytes is None:
                            xml_bytes = inner_zf.read(inner_info.filename)
                        elif ext in IMAGE_EXTENSIONS:
                            images[name_lower] = inner_zf.read(inner_info.filename)

                    if xml_bytes and images:
                        parsed = parse_spl_xml(xml_bytes, images)
                        if parsed and parsed.images:
                            yield parsed
            except Exception as exc:
                logger.warning("Error reading inner archive %s: %s", info.filename, exc)
