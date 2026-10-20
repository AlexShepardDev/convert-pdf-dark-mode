#!/usr/bin/env python3
"""Make a dark mode copy of a PDF.

Every page gets a dark background and light text. The text layer is not touched,
so the copy stays selectable and searchable. Photos and figures keep their colors;
full-page scans are darkened together with the page.

How it works: the original page content is drawn on a white sheet, then three
full-page rectangles are blended over it - Difference with white inverts the page,
Lighten and Darken pull the result into a soft dark range. Opaque images are
pre-inverted through their /Decode array, so the page inversion brings them back
to their own colors.

Usage:
    python3 dark_pdf.py input.pdf [-o output.pdf] [--images auto|keep|invert]
                        [--password PASSWORD] [--preview page1.png]

Needs pypdf. The preview needs pypdfium2 and is skipped without it.
"""

import argparse
import logging
import os
import sys

try:
    from pypdf import PdfReader, PdfWriter
    from pypdf.errors import PdfReadError
    from pypdf.generic import (
        ArrayObject,
        ByteStringObject,
        DecodedStreamObject,
        DictionaryObject,
        NameObject,
        NumberObject,
        StreamObject,
    )
except ImportError:
    sys.stderr.write("pypdf is not installed. Run: pip install pypdf\n")
    sys.exit(3)

logging.getLogger("pypdf").setLevel(logging.ERROR)  # repair notes on damaged files are not errors

BACKGROUND = 0x1F / 255  # darkest tone of the copy, #1f1f1f
TEXT = 0xCC / 255  # lightest tone of the copy, #cccccc

GS_DIFFERENCE = "/CPDMDiff"
GS_LIGHTEN = "/CPDMLight"
GS_DARKEN = "/CPDMDark"

# A page scan is a large opaque image with the shape of the page that looks like
# paper: light and nearly colorless. Covers and full-page photos keep their colors.
SCAN_MIN_SIDE = 600
SCAN_RATIO_TOLERANCE = 0.12
PAPER_MIN_LIGHTNESS = 0.6
PAPER_MAX_SATURATION = 0.25

GRAY_SPACES = {"/DeviceGray", "/CalGray", "/G"}
RGB_SPACES = {"/DeviceRGB", "/CalRGB", "/RGB"}


def resolve(obj):
    return obj.get_object() if obj is not None else None


def inherited(page, key):
    node = page
    while node is not None:
        if key in node:
            return node[key]
        node = resolve(node.get("/Parent"))
    return None


def page_box(page):
    box = resolve(inherited(page, "/MediaBox"))
    if box is None:
        return 0.0, 0.0, 612.0, 792.0
    x0, y0, x1, y1 = (float(v) for v in box)
    return min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0)


def filters_of(stream):
    f = resolve(stream.get("/Filter"))
    if f is None:
        return []
    if isinstance(f, ArrayObject):
        return [str(resolve(x)) for x in f]
    return [str(f)]


def components(cs):
    """Number of color components for a non-indexed image color space, or None if
    inverting it through /Decode would not give a dark copy of the image."""
    cs = resolve(cs)
    if cs is None:
        return None
    if isinstance(cs, NameObject):
        if cs in GRAY_SPACES:
            return 1
        if cs in RGB_SPACES:
            return 3
        return None
    if isinstance(cs, ArrayObject) and len(cs) > 0:
        kind = str(resolve(cs[0]))
        if kind in GRAY_SPACES:
            return 1
        if kind in RGB_SPACES:
            return 3
        if kind == "/ICCBased" and len(cs) > 1:
            n = int(resolve(cs[1]).get("/N", 0))
            return n if n in (1, 3) else None
    return None


def lookup_bytes(lookup):
    lookup = resolve(lookup)
    if isinstance(lookup, StreamObject):
        return lookup.get_data()
    if hasattr(lookup, "original_bytes"):
        return lookup.original_bytes
    return bytes(lookup)


def looks_like_paper(img):
    if int(img.get("/BitsPerComponent", 8)) == 1:
        return True  # black-and-white scan
    try:
        from PIL import ImageStat

        picture = img.decode_as_image()
        picture.thumbnail((96, 96))
        _, saturation, lightness = (v / 255 for v in ImageStat.Stat(picture.convert("RGB").convert("HSV")).mean)
    except Exception:  # no Pillow or an unusual encoding: judge by color alone
        return components(img.get("/ColorSpace")) == 1
    return lightness >= PAPER_MIN_LIGHTNESS and saturation <= PAPER_MAX_SATURATION


def is_page_scan(img, page_w, page_h):
    w, h = int(img.get("/Width", 0)), int(img.get("/Height", 0))
    if min(w, h) < SCAN_MIN_SIDE or page_w <= 0 or page_h <= 0:
        return False
    ratio = w / h
    if not any(abs(ratio / target - 1) <= SCAN_RATIO_TOLERANCE for target in (page_w / page_h, page_h / page_w)):
        return False
    return looks_like_paper(img)


def follows_page(img):
    """Stencils, logos and icons with transparency are recolored with the page:
    a black logo on a white page becomes a light logo on a dark page."""
    return bool(img.get("/ImageMask")) or any(k in img for k in ("/SMask", "/Mask", "/SMaskInData"))


def preinvert_image(img):
    """Invert an opaque image so that the page inversion restores it.
    Returns a short reason when the image cannot be kept."""
    if "/JPXDecode" in filters_of(img):
        return "jpx"  # JPEG 2000 ignores /Decode
    cs = resolve(img.get("/ColorSpace"))
    if isinstance(cs, ArrayObject) and len(cs) == 4 and str(resolve(cs[0])) == "/Indexed":
        if components(cs[1]) is None:
            return "colorspace"
        inverted = bytes(255 - b for b in lookup_bytes(cs[3]))
        img[NameObject("/ColorSpace")] = ArrayObject(
            [NameObject("/Indexed"), cs[1], NumberObject(int(resolve(cs[2]))), ByteStringObject(inverted)]
        )
        return None
    n = components(cs)
    if n is None:
        return "colorspace"
    decode = resolve(img.get("/Decode"))
    if isinstance(decode, ArrayObject) and len(decode) == 2 * n:
        values = [resolve(v) for v in decode]
        swapped = []
        for i in range(0, 2 * n, 2):
            swapped += [values[i + 1], values[i]]
        img[NameObject("/Decode")] = ArrayObject(swapped)
    else:
        img[NameObject("/Decode")] = ArrayObject([NumberObject(1), NumberObject(0)] * n)
    return None


def walk_resources(res, page_w, page_h, mode, seen, stats, depth=0):
    res = resolve(res)
    if res is None or depth > 12:
        return
    xobjects = resolve(res.get("/XObject"))
    if xobjects is not None:
        for ref in xobjects.values():
            obj = resolve(ref)
            key = ref.idnum if hasattr(ref, "idnum") else id(obj)
            if key in seen or obj is None:
                continue
            seen.add(key)
            subtype = obj.get("/Subtype")
            if subtype == "/Image":
                handle_image(obj, page_w, page_h, mode, stats)
            elif subtype == "/Form":
                walk_resources(obj.get("/Resources"), page_w, page_h, mode, seen, stats, depth + 1)
    patterns = resolve(res.get("/Pattern"))
    if patterns is not None:
        for ref in patterns.values():
            pattern = resolve(ref)
            if pattern is not None and int(pattern.get("/PatternType", 0)) == 1:
                walk_resources(pattern.get("/Resources"), page_w, page_h, mode, seen, stats, depth + 1)


def handle_image(img, page_w, page_h, mode, stats):
    if mode == "invert":
        stats["darkened"] += 1
        return
    if follows_page(img):
        stats["follow_page"] += 1
        return
    if mode == "auto" and is_page_scan(img, page_w, page_h):
        stats["scans"] += 1
        return
    reason = preinvert_image(img)
    if reason is None:
        stats["kept"] += 1
    else:
        stats["not_kept"][reason] = stats["not_kept"].get(reason, 0) + 1


def page_content(page):
    contents = resolve(page.get("/Contents"))
    if contents is None:
        return b""
    if isinstance(contents, ArrayObject):
        return b"\n".join(resolve(part).get_data() for part in contents)
    return contents.get_data()


def add_blend_states(page):
    res = inherited(page, "/Resources")
    if res is None:
        res = DictionaryObject()
    page[NameObject("/Resources")] = res
    res = resolve(res)
    if "/ExtGState" not in res:
        res[NameObject("/ExtGState")] = DictionaryObject()
    states = resolve(res["/ExtGState"])
    for name, mode in ((GS_DIFFERENCE, "/Difference"), (GS_LIGHTEN, "/Lighten"), (GS_DARKEN, "/Darken")):
        states[NameObject(name)] = DictionaryObject(
            {NameObject("/Type"): NameObject("/ExtGState"), NameObject("/BM"): NameObject(mode)}
        )


def darken_page(writer, page):
    x, y, w, h = page_box(page)
    rect = f"{x:.3f} {y:.3f} {w:.3f} {h:.3f} re f".encode()
    head = b"q 1 g " + rect + b" Q\nq\n"
    tail = (
        b"\nQ\nq "
        + GS_DIFFERENCE.encode() + b" gs 1 g " + rect + b" "
        + GS_LIGHTEN.encode() + f" gs {BACKGROUND:.4f} g ".encode() + rect + b" "
        + GS_DARKEN.encode() + f" gs {TEXT:.4f} g ".encode() + rect
        + b" Q\n"
    )
    add_blend_states(page)
    stream = DecodedStreamObject()
    stream.set_data(head + page_content(page) + tail)
    page[NameObject("/Contents")] = writer._add_object(stream)
    try:
        page.compress_content_streams(level=9)
    except TypeError:  # older pypdf without the level argument
        page.compress_content_streams()


def render_preview(pdf_path, png_path):
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return "skipped: pypdfium2 is not installed"
    doc = pdfium.PdfDocument(pdf_path)
    try:
        doc[0].render(scale=1.5).to_pil().save(png_path)
    except Exception as error:  # no Pillow, for example: the copy itself is already saved
        return f"skipped: {error.__class__.__name__}: {error}"
    finally:
        doc.close()
    return png_path


def main():
    parser = argparse.ArgumentParser(description="Make a dark mode copy of a PDF.")
    parser.add_argument("input", help="PDF to convert")
    parser.add_argument("-o", "--output", help="where to save the copy (default: <input>-dark.pdf)")
    parser.add_argument(
        "--images",
        choices=("auto", "keep", "invert"),
        default="auto",
        help="auto: keep photos and figures, darken page scans (default); "
        "keep: keep every image as it is; invert: darken images too",
    )
    parser.add_argument("--password", default="", help="password for a protected PDF")
    parser.add_argument("--preview", help="also render page 1 of the copy to this PNG")
    args = parser.parse_args()

    output = args.output or os.path.splitext(args.input)[0] + "-dark.pdf"

    try:
        reader = PdfReader(args.input, strict=False)
    except (PdfReadError, OSError, ValueError) as error:
        print(f"Error: cannot read {args.input}: {error}")
        return 1
    if reader.is_encrypted and not reader.decrypt(args.password):
        print("Error: the PDF is password-protected. Ask the user for the password and run again with --password.")
        return 2

    writer = PdfWriter(clone_from=reader)
    stats = {"kept": 0, "scans": 0, "darkened": 0, "follow_page": 0, "not_kept": {}}
    seen = set()
    skipped = []
    for number, page in enumerate(writer.pages, start=1):
        try:
            _, _, w, h = page_box(page)
            walk_resources(inherited(page, "/Resources"), w, h, args.images, seen, stats)
            darken_page(writer, page)
        except Exception as error:  # a broken page stays as it was, the rest is converted
            skipped.append(f"{number} ({error.__class__.__name__})")

    try:
        writer.compress_identical_objects()  # drops duplicate and unused objects, the copy gets smaller
    except Exception:  # older pypdf: the copy is only larger
        pass
    with open(output, "wb") as f:
        writer.write(f)

    pages = len(writer.pages)
    print(f"Saved: {output}")
    print(f"Pages converted: {pages - len(skipped)} of {pages}")
    if skipped:
        print(f"Pages left as they were (could not be read): {', '.join(skipped)}")
    print("Text: selectable and searchable, as in the original")
    print(f"Images kept in their own colors: {stats['kept']}")
    if stats["scans"]:
        print(f"Page scans darkened with the page: {stats['scans']} (use --images keep to keep them)")
    if stats["darkened"]:
        print(f"Images darkened with the page: {stats['darkened']}")
    if stats["follow_page"]:
        print(f"Icons and logos with transparency recolored with the page: {stats['follow_page']}")
    if stats["not_kept"]:
        detail = ", ".join(f"{count} {reason}" for reason, count in sorted(stats["not_kept"].items()))
        print(f"Images that could not be kept and look inverted: {detail}")
    if args.preview:
        print(f"Preview of page 1: {render_preview(output, args.preview)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
