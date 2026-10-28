"""A practical estimate of a brand's marketing spend last quarter, for rows the sheet leaves blank.

The brand guide's template quotes "your estimated range of spend on marketing was ₹X–Y lakhs". The sheet
has no spend data, so the app estimates a range from what the row (and, when it was read, the brand's
website and Instagram) shows about the size of the business. The figure is always called an estimate in
the email, the basis is saved as the contact's Spend source (the guide's reply rules quote it when a
brand asks where it came from), and a person can overwrite it on the contact at any time.

The ranges are deliberately modest: a small D2C wellness brand in India typically spends a few lakhs a
quarter on ads and creators, an established one tens of lakhs. An estimate that is too high reads as
careless; one in the right band starts the conversation.
"""
import csv
import re

from .. import config
from . import marketplace
from .research import FREE_MAIL

# (low, high) in ₹ lakhs per quarter, smallest business first.
TIERS = [(0.5, 1.5), (1, 3), (2, 5), (3, 8), (5, 12), (10, 25), (20, 50), (40, 100)]
# Instagram followers -> the smallest tier a brand that size is likely to be in.
FOLLOWER_TIERS = [(1_000_000, 7), (300_000, 6), (100_000, 5), (40_000, 4), (10_000, 3), (3_000, 2), (0, 1)]
FOLLOWERS_RE = re.compile(r"([\d][\d.,]*)\s*([KkMm])?\s+Followers", re.I)
LINKEDIN_RE = re.compile(r"([\d][\d.,]*)\s*([KkMm])?\s+followers on LinkedIn", re.I)
REFERENCE = "templates/brand_reference.csv"
PRICE_RE = re.compile(r"(?:₹|rs\.?|inr)\s*([\d,]+)", re.I)
# Categories where brands lean hardest on creators and performance ads.
AD_HEAVY = {"PRO", "PR", "SUP"}
SOURCE_PREFIX = "Dr. Tracker estimate from public signals"
# Scale signals on the brand's own website (Instagram refuses automated reads, so followers are rarely known).
SITE_SIGNALS = [
    (re.compile(r"\b(blinkit|zepto|instamart|bigbasket|big basket|jiomart|swiggy instamart)\b", re.I), "sold on quick-commerce apps"),
    (re.compile(r"\b(amazon|flipkart|nykaa|tata 1mg|1mg|myntra|healthkart)\b", re.I), "sold on marketplaces"),
    (re.compile(r"\bshark tank\b", re.I), "featured on Shark Tank"),
    (re.compile(r"\b(\d[\d,]*\+?\s*(retail )?(stores|outlets|cities|pin ?codes)|available (in|at) (stores|retail)|offline stores)\b", re.I),
     "offline retail presence"),
]


def _count(m: re.Match) -> int:
    n = float(m.group(1).replace(",", ""))
    return int(n * {"k": 1_000, "m": 1_000_000}.get((m.group(2) or "").lower(), 1))


def _sections(text: str, label: str) -> str:
    """The research excerpt's sections for one source ("[Instagram https://…]" up to the next "[…]" header)."""
    parts = re.split(r"^(?=\[[A-Z][\w ]* https?://)", text or "", flags=re.M)
    return "\n".join(p for p in parts if p.startswith(f"[{label} "))


def followers(text: str) -> int | None:
    """Instagram followers, when the research excerpt has them (LinkedIn's are counted separately)."""
    counts = [_count(m) for m in FOLLOWERS_RE.finditer(LINKEDIN_RE.sub(" ", _sections(text, "Instagram")))]
    return max(counts) if counts else None


def linkedin_followers(text: str) -> int | None:
    counts = [_count(m) for m in LINKEDIN_RE.finditer(text or "")]
    return max(counts) if counts else None


_reference: tuple[float, dict] | None = None


def reference_rows() -> dict:
    """templates/brand_reference.csv by brand key: well-known brands' spend, and rows to skip."""
    global _reference
    from .importer import brand_key  # importer imports research and marketplace, not this module

    path = config.ROOT / REFERENCE
    mtime = path.stat().st_mtime if path.is_file() else 0.0
    if _reference is None or _reference[0] != mtime:
        rows = {}
        if mtime:
            with path.open(encoding="utf-8") as f:
                for row in csv.DictReader(line for line in f if not line.startswith("#")):
                    if (key := brand_key(row.get("name") or "")):
                        rows[key] = {k: (v or "").strip() for k, v in row.items() if k}
        _reference = (mtime, rows)
    return _reference[1]


def reference(contact: dict) -> dict | None:
    from .importer import brand_key

    return reference_rows().get(brand_key(contact.get("name") or ""))


def _products(cell: str) -> int:
    return len([p for p in re.split(r"\s*[;/,]\s*", cell or "") if p.strip()])


def _top_price(cell: str) -> int:
    prices = [int(p.replace(",", "")) for p in PRICE_RE.findall(cell or "") if p.replace(",", "").isdigit()]
    return max(prices) if prices else 0


def estimate(contact: dict) -> tuple[float, float, str]:
    """(low, high, basis) in ₹ lakhs for last quarter."""
    ref = reference(contact)
    if ref and (rng := marketplace.spend_range(ref.get("spend_lakhs_per_quarter") or "")):
        return rng[0], rng[1], f"{SOURCE_PREFIX}: {ref.get('basis') or 'a well-known brand'}"
    signals, score = [], 0
    domain = (contact.get("email") or "").rsplit("@", 1)[-1].lower()
    if (contact.get("website") or "").strip():
        score += 1
        signals.append("own website")
    if domain and domain not in FREE_MAIL:
        score += 1
    elif domain:
        signals.append("a free-mail inbox")
    if re.search(r"instagram|^@", contact.get("socials") or "", re.I):
        score += 1
        signals.append("Instagram")
    if (n := _products(contact.get("product") or "")) >= 3:
        score += 1
        signals.append(f"{n} products listed")
    if _top_price(contact.get("pricing") or "") >= 1500:
        score += 1
        signals.append("premium price points")
    if (contact.get("category") or "").upper() in AD_HEAVY:
        score += 1
        signals.append("an ad-heavy category")
    site = contact.get("site_text") or ""
    for rx, label in SITE_SIGNALS:
        if rx.search(site):
            score += 1
            signals.append(label)
    # 0–1 signals: ₹0.5–1.5 lakhs; a typical D2C brand (site, own domain, Instagram, ad-heavy category): ₹3–8;
    # add quick commerce, marketplaces, Shark Tank and offline retail and it reaches ₹20–50.
    tier = max(0, min(score - 1, 6))
    if (li := linkedin_followers(site)) and li >= 5_000:
        score += 1
        signals.append(f"about {_short(li)} LinkedIn followers")
    count = followers(site)
    if count is not None:
        floor = next(t for limit, t in FOLLOWER_TIERS if count >= limit)
        # Followers say most about size: use them, nudged one tier by the rest.
        tier = max(floor, min(floor + 1, tier)) if count >= 3_000 else min(tier, 1)
        signals.insert(0, f"about {_short(count)} Instagram followers")
    low, high = TIERS[tier]
    return low, high, f"{SOURCE_PREFIX}: " + (", ".join(signals) if signals else "a small online presence")


def _short(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M".replace(".0M", "M")
    if n >= 1_000:
        return f"{round(n / 1_000)}K"
    return str(n)


def is_estimate(contact: dict) -> bool:
    return (contact.get("spend_source") or "").startswith(SOURCE_PREFIX)


def needs_estimate(contact: dict) -> bool:
    """A brand with no spend figure. Once estimated it stays, so every touch quotes the same figure."""
    return contact.get("type") == "brand" and marketplace.spend_range(contact.get("spend") or "") is None


def as_cell(low: float, high: float) -> str:
    return f"{marketplace.lakhs(low)}-{marketplace.lakhs(high)}".replace(",", "")
