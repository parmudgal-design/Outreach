"""Per-audience email templates: the writing guide Claude follows for a brand's or a
professional's emails, and the images placed in the first email's body.

Brands use the files at the repo root and nothing else: `brand_outreach_agent.md`
is the guide, and the three product-flow screenshots (`Step 1 – Asks in chat@2x.png`,
`Step 2 – Sees it first@2x.png`, `Step 3 – Buys with a code@2x.png`) go into every
first email, in that order. Professionals use `templates/professional/` (guide.md,
banner.png or .jpg) once someone adds them.
Files saved under Setup → Email templates go to `<data dir>/templates/<audience>/`
and win over the built-in ones, so a guide or banner can be added or replaced
without a code change. An audience with no guide uses the built-in marketplace
pitch (prompts.py); one with no images sends plain-text email.

The draft marks where the images go with `[HOW_IT_WORKS_IMAGE]` on its own
line. The marker stays in the draft people review; mailer.build_message swaps
it for the images when the email is sent.
"""
import io
import re
from dataclasses import dataclass
from pathlib import Path

from .. import config

AUDIENCES = ("brand", "professional")
MARKER = "[HOW_IT_WORKS_IMAGE]"
# [HOW_IT_WORKS_IMAGE] puts every image in one place; [SCREENSHOT_1], [SCREENSHOT_2]… put each image on its own.
MARKER_RE = re.compile(r"^[ \t]*\[(?:HOW_IT_WORKS_IMAGE|BANNER|SCREENSHOT_(\d))\][ \t]*$", re.M | re.I)


def screenshot_marker(n: int) -> str:
    return f"[SCREENSHOT_{n}]"
BANNER_EXTS = {".png": "png", ".jpg": "jpeg", ".jpeg": "jpeg", ".gif": "gif"}
# Wide enough to stay sharp on a retina screen at the 600px an email column usually allows, small enough
# (~200 KB as JPEG) not to hurt deliverability. The original upload is kept as it was.
EMAIL_WIDTH = 1200
MAX_UPLOAD = 15 * 1024 * 1024
MAX_GUIDE_CHARS = 60_000
ALT_TEXT = {"brand": "From chat to checkout: how Dr. Tracker works", "professional": "How Dr. Tracker works"}
BRAND_GUIDE = "brand_outreach_agent.md"
# (file at the repo root, filename in the email, alt text): the order and alt text from the guide's section 5.
BRAND_SCREENSHOTS = [
    ("Step 1 – Asks in chat@2x.png", "Step_1___Asks_in_chat_2x",
     "Step 1 of 3: Shopper asks the Dr. Tracker assistant what protein to buy and is pointed to a matched product."),
    ("Step 2 – Sees it first@2x.png", "Step_2___Sees_it_first_2x",
     "Step 2 of 3: The matched product leads the shopper's personalised shop, filtered by diet, taste and budget."),
    ("Step 3 – Buys with a code@2x.png", "Step_3___Buys_with_a_code_2x",
     "Step 3 of 3: Product page with prices across stores and the DRTRACKER10 checkout code."),
]


class TemplateError(Exception):
    pass


@dataclass
class Banner:
    data: bytes         # email-ready (resized) image
    subtype: str        # image/<subtype>
    width: int
    height: int
    source: str         # custom | built-in
    alt: str
    name: str = "dr-tracker"  # filename in the email, without the extension

    @property
    def filename(self) -> str:
        return f"{self.name}.{'jpg' if self.subtype == 'jpeg' else self.subtype}"


def builtin_dir(audience: str) -> Path:
    return config.ROOT / "templates" / audience


def builtin_guide_path(audience: str) -> Path:
    return config.ROOT / BRAND_GUIDE if audience == "brand" else builtin_dir(audience) / "guide.md"


def builtin_images(audience: str) -> list[tuple[Path, str, str]]:
    """(path, filename, alt) of the shipped images that exist, in email order."""
    if audience == "brand":
        return [(config.ROOT / f, name, alt) for f, name, alt in BRAND_SCREENSHOTS if (config.ROOT / f).is_file()]
    for ext in BANNER_EXTS:
        p = builtin_dir(audience) / f"banner{ext}"
        if p.is_file():
            return [(p, "dr-tracker", ALT_TEXT[audience])]
    return []


def custom_dir(audience: str) -> Path:
    # Next to the database, so tests (and a second instance) get their own copy.
    return config.DB_PATH.parent / "templates" / audience


def _check(audience: str) -> None:
    if audience not in AUDIENCES:
        raise TemplateError(f"Unknown audience {audience!r} (brand or professional).")


def guide(audience: str) -> tuple[str, str]:
    """(guide text, 'custom' | 'built-in'), or ('', '') when this audience has none."""
    if audience not in AUDIENCES:
        return "", ""
    for source, p in (("custom", custom_dir(audience) / "guide.md"), ("built-in", builtin_guide_path(audience))):
        if p.is_file():
            text = p.read_text(encoding="utf-8").strip()
            if text:
                return text, source
    return "", ""


def _image_files(audience: str) -> tuple[list[tuple[Path, str, str]], str]:
    """The image files a first email carries and where they come from: an uploaded banner replaces the
    shipped images."""
    d = custom_dir(audience)
    for ext in BANNER_EXTS:
        p = d / f"banner{ext}"
        if p.is_file():
            return [(p, "dr-tracker", ALT_TEXT[audience])], "custom"
    files = builtin_images(audience)
    return files, "built-in" if files else ""


def has_banner(audience: str) -> bool:
    """Cheap check (no image decoding) for the pre-send checks."""
    return audience in AUDIENCES and bool(_image_files(audience)[0])


_cache: dict[tuple, Banner] = {}


def images(audience: str) -> list[Banner]:
    """The images as they go into a first email, in order; empty when this audience has none."""
    if audience not in AUDIENCES:
        return []
    files, source = _image_files(audience)
    out = []
    for path, name, alt in files:
        key = (str(path), path.stat().st_mtime_ns)
        if key not in _cache:
            data, subtype, w, h = email_ready(path.read_bytes())
            _cache[key] = Banner(data, subtype, w, h, source, alt, name)
        out.append(_cache[key])
    if len(_cache) > 16:  # never keep replaced uploads around for long
        live = {(str(p), p.stat().st_mtime_ns) for p, _, _ in files}
        for k in [k for k in _cache if k not in live]:
            del _cache[k]
    return out


def banner(audience: str) -> Banner | None:
    """The first image, or None when this audience has none."""
    found = images(audience)
    return found[0] if found else None


def email_ready(raw: bytes) -> tuple[bytes, str, int, int]:
    """Resize to EMAIL_WIDTH and re-encode as JPEG (screenshots with text stay crisp at quality 88)."""
    from PIL import Image

    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception as e:
        raise TemplateError(f"That isn't an image the app can read ({e}).") from e
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        flat = Image.new("RGB", img.size, (255, 255, 255))
        flat.paste(img, mask=img.getchannel("A"))
        img = flat
    else:
        img = img.convert("RGB")
    if img.width > EMAIL_WIDTH:
        img = img.resize((EMAIL_WIDTH, round(img.height * EMAIL_WIDTH / img.width)), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, "JPEG", quality=88, optimize=True, progressive=True)
    return out.getvalue(), "jpeg", img.width, img.height


def save_guide(audience: str, text: str) -> None:
    _check(audience)
    text = (text or "").strip()
    if not text:
        raise TemplateError("The guide is empty. Use “Reset to built-in” to go back to the shipped one.")
    if len(text) > MAX_GUIDE_CHARS:
        raise TemplateError(f"The guide is over {MAX_GUIDE_CHARS:,} characters.")
    d = custom_dir(audience)
    d.mkdir(parents=True, exist_ok=True)
    (d / "guide.md").write_text(text + "\n", encoding="utf-8")


def save_banner(audience: str, data: bytes, filename: str) -> None:
    _check(audience)
    ext = Path(filename or "").suffix.lower()
    if ext not in BANNER_EXTS:
        raise TemplateError("Upload the banner as PNG, JPG or GIF.")
    if len(data) > MAX_UPLOAD:
        raise TemplateError("That image is over 15 MB.")
    email_ready(data)  # raises if it isn't a readable image
    d = custom_dir(audience)
    d.mkdir(parents=True, exist_ok=True)
    for old in BANNER_EXTS:
        (d / f"banner{old}").unlink(missing_ok=True)
    (d / f"banner{ext}").write_bytes(data)


def reset(audience: str, part: str) -> None:
    """Remove the custom guide or banner, so the built-in one (if any) is used again."""
    _check(audience)
    d = custom_dir(audience)
    if part == "guide":
        (d / "guide.md").unlink(missing_ok=True)
    elif part == "banner":
        for ext in BANNER_EXTS:
            (d / f"banner{ext}").unlink(missing_ok=True)
    else:
        raise TemplateError(f"Unknown part {part!r} (guide or banner).")


def has_builtin(audience: str, part: str) -> bool:
    if part == "guide":
        return builtin_guide_path(audience).is_file()
    return bool(builtin_images(audience))


def status() -> dict:
    out = {}
    for a in AUDIENCES:
        text, gsrc = guide(a)
        try:
            imgs = images(a)
            berr = ""
        except TemplateError as e:
            imgs, berr = [], str(e)
        b = imgs[0] if imgs else None
        out[a] = {
            "guide": text, "guide_source": gsrc, "guide_builtin": has_builtin(a, "guide"),
            "banner_source": b.source if b else "", "banner_builtin": has_builtin(a, "banner"),
            "banner_size": sum(len(i.data) for i in imgs), "banner_dims": [b.width, b.height] if b else None,
            "banner_error": berr, "marker": MARKER,
            "images": [{"name": i.filename, "alt": i.alt, "size": len(i.data), "dims": [i.width, i.height]} for i in imgs],
        }
    return out


def segments(body: str, count: int) -> list:
    """The body as text and image positions, in order: strings for text, 0-based ints for images. Every image
    appears exactly once: a numbered marker places that image, [HOW_IT_WORKS_IMAGE] places the rest, and any
    image no marker placed follows the last one that was (or goes where place_marker would put it)."""
    body = body or ""
    if count and not MARKER_RE.search(body):
        body = place_marker(body)
    out: list = []
    pos, used = 0, set()
    for m in MARKER_RE.finditer(body):
        out.append(body[pos:m.start()])
        pos = m.end()
        if m.group(1):
            idx = [int(m.group(1)) - 1]
        else:
            idx = list(range(count))
        idx = [i for i in idx if 0 <= i < count and i not in used]
        out.extend(idx)
        used.update(idx)
    out.append(body[pos:])
    missing = [i for i in range(count) if i not in used]
    if missing:
        last = max((k for k, x in enumerate(out) if isinstance(x, int)), default=None)
        at = last + 1 if last is not None else 1
        out[at:at] = missing
    return [x for x in out if not (isinstance(x, str) and not x.strip())]


def place_marker(body: str) -> str:
    """The body with the marker in it: as written, or (when a person removed it) after the numbered steps,
    or else after the first paragraph following the greeting."""
    if MARKER_RE.search(body or ""):
        return body
    # The brand guide puts the screenshots right below its numbered "how it works" list.
    steps = list(re.finditer(r"^[ \t]*\d[.)][ \t].*$", body or "", re.M))
    if len(steps) >= 2:
        end = steps[-1].end()
        return body[:end] + "\n\n" + MARKER + body[end:]
    paras = re.split(r"(\n\s*\n)", body or "")
    # paras alternates text, separator, text, …: greeting at 0, the pitch at 2.
    idx = 2 if len(paras) > 3 else len(paras) - 1
    paras[idx] = paras[idx].rstrip() + "\n\n" + MARKER
    return "".join(paras)
