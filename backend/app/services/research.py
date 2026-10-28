"""Research: short plain-text excerpts of a partner's own website and social
profiles, so the first email can mention what they actually sell or do, not
just their category.

Sources, in order:
  * the website (the Website field, or failing that the email domain unless
    it's a free-mail provider): the homepage plus, when it links to them, one
    "about" page and one "products"/"shop" page;
  * every social profile in the row (the Socials field, and any link in the
    sheet's other columns): Instagram, LinkedIn, Facebook, X, YouTube, Linktree.
    Most of these show only a title and a description without a login, and some
    refuse outright; that's noted and the rest is used.
Only public http(s) addresses are fetched, every redirect hop is checked too,
and each download is size-capped. The excerpts are the partner's own marketing:
the prompt tells Claude to use them only to be specific and never to repeat
their claims, and a person still approves the draft.
"""
import ipaddress
import json
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

FREE_MAIL = {
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.in", "yahoo.in", "ymail.com", "rocketmail.com",
    "outlook.com", "outlook.in", "hotmail.com", "live.com", "msn.com", "icloud.com", "me.com", "aol.com",
    "rediffmail.com", "rediff.com", "proton.me", "protonmail.com", "zohomail.in", "gmx.com", "mail.com",
}
MAX_BYTES = 600_000
MAX_CHARS = 2500
TIMEOUT = 10
REFRESH_DAYS = 30
USER_AGENT = "Mozilla/5.0 (compatible; DrTrackerOutreach/1.0; +https://dr-tracker.com)"
# Error, bot-check and "turn on JavaScript" pages: worse than nothing for the drafter.
NOT_CONTENT_RE = re.compile(
    r"page (you're|you are) looking for|page not found|\b404\b|something (here )?is broken|enable javascript|"
    r"javascript (is )?(required|disabled)|access denied|are you a (human|robot)|captcha|just a moment|checking your browser",
    re.I,
)
SKIP_TAGS = {"script", "style", "noscript", "svg", "nav", "footer", "header", "form", "button", "select", "template", "iframe"}
# Inner pages worth reading after the homepage: one of each kind, same site only.
INNER_PAGES = [
    re.compile(r"/(about|about-us|our-story|story|who-we-are|brand-story)(/|$|\.html?)", re.I),
    re.compile(r"/(products?|shop|shop-all|collections?(/all)?|store|range)(/|$|\.html?)", re.I),
]
INNER_CHARS = 900
SOCIAL_CHARS = 500
MAX_SOCIALS = 3
SOCIAL_SITES = {
    "instagram.com": "Instagram", "linkedin.com": "LinkedIn", "facebook.com": "Facebook", "fb.com": "Facebook",
    "x.com": "X", "twitter.com": "X", "youtube.com": "YouTube", "linktr.ee": "Linktree",
}
# What a profile shows instead of the profile when it wants a login.
LOGIN_WALL_RE = re.compile(r"^(instagram|linkedin|facebook|x|youtube)$|\blog ?in(to)?\b|\bsign ?(in|up)\b|join linkedin|create an account", re.I)
URL_IN_TEXT_RE = re.compile(r"https?://[^\s,;|<>\"']+|(?<![\w@.])(?:www\.)?(?:instagram|linkedin|facebook|twitter|x|youtube)\.com/[^\s,;|<>\"']+|linktr\.ee/[^\s,;|<>\"']+", re.I)
HANDLE_RE = re.compile(r"^@([A-Za-z0-9._]{2,30})$")


class ResearchError(Exception):
    pass


def site_url(contact: dict) -> str:
    """The homepage to read for this contact, or '' when there's nothing sensible to fetch."""
    site = (contact.get("website") or "").strip()
    if not site:
        domain = (contact.get("email") or "").rpartition("@")[2].strip().lower()
        if not domain or domain in FREE_MAIL or "." not in domain:
            return ""
        site = domain
    if not re.match(r"^https?://", site, re.I):
        site = "https://" + site.lstrip("/")
    parts = urllib.parse.urlsplit(site)
    if not parts.hostname:
        return ""
    return urllib.parse.urlunsplit((parts.scheme.lower(), parts.netloc, parts.path or "/", parts.query, ""))


def is_due(contact: dict, now: float | None = None) -> bool:
    checked = contact.get("site_checked_at")
    return not checked or (now or time.time()) - checked > REFRESH_DAYS * 86400


def _check_public(url: str) -> None:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ResearchError(f"Not a web address: {url}")
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80))
    except (socket.gaierror, UnicodeError) as e:
        raise ResearchError(f"{parts.hostname} doesn't resolve") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise ResearchError(f"{parts.hostname} points at a private address; not fetched")


class _CheckedRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _check_public(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_CheckedRedirects)


def fetch_html(url: str) -> str:
    _check_public(url)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"})
    try:
        with _opener.open(req, timeout=TIMEOUT) as resp:
            ctype = resp.headers.get("Content-Type", "")
            if "html" not in ctype.lower():
                raise ResearchError(f"Not a web page ({ctype or 'unknown type'})")
            data = resp.read(MAX_BYTES)
            charset = resp.headers.get_content_charset() or "utf-8"
    except urllib.error.HTTPError as e:
        raise ResearchError(f"The site answered {e.code}") from e
    except (urllib.error.URLError, OSError) as e:
        reason = getattr(e, "reason", e)
        raise ResearchError(f"Couldn't reach the site ({reason})") from e
    try:
        return data.decode(charset, errors="replace")
    except LookupError:
        return data.decode("utf-8", errors="replace")


class _Extractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.description = ""
        self.og_title = ""
        self.headings: list[str] = []
        self.text: list[str] = []
        self.links: list[str] = []
        self._skip = 0
        self._in_title = False
        self._heading = None

    def handle_starttag(self, tag, attrs):
        if tag == "a":  # menus are skipped as text, but they're where the About and Shop links live
            href = dict(attrs).get("href") or ""
            if href and len(self.links) < 400:
                self.links.append(href)
        if tag in SKIP_TAGS:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag in ("h1", "h2"):
            self._heading = []
        elif tag == "meta":
            a = {k.lower(): (v or "") for k, v in attrs}
            if a.get("name", "").lower() == "description" or a.get("property", "").lower() == "og:description":
                self.description = self.description or a.get("content", "").strip()
            elif a.get("property", "").lower() == "og:title":
                self.og_title = self.og_title or a.get("content", "").strip()

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in ("h1", "h2") and self._heading is not None:
            h = " ".join("".join(self._heading).split())
            if h and h not in self.headings:
                self.headings.append(h)
            self._heading = None

    def handle_data(self, data):
        if self._in_title:
            self.title += data
            return
        if self._skip:
            return
        if self._heading is not None:
            self._heading.append(data)
        s = " ".join(data.split())
        if len(s) > 2:
            self.text.append(s)


def parse(html: str) -> _Extractor:
    p = _Extractor()
    try:
        p.feed(html)
        p.close()
    except Exception:  # malformed pages: keep whatever was parsed
        pass
    return p


def _cap(text: str, limit: int) -> str:
    return text[:limit].rsplit(" ", 1)[0] + " …" if len(text) > limit else text


def extract(html: str, limit: int = MAX_CHARS) -> str:
    """Title, meta description, headings and body text, as one short block of plain text."""
    return _summary(parse(html), limit)


def _summary(p: _Extractor, limit: int = MAX_CHARS) -> str:
    lines = []
    if p.title.strip():
        lines.append("Title: " + " ".join(p.title.split()))
    if p.description:
        lines.append("Description: " + " ".join(p.description.split()))
    if p.headings:
        lines.append("Headings: " + " | ".join(p.headings[:8]))
    body, seen = [], set()
    for t in p.text:
        if t not in seen:
            seen.add(t)
            body.append(t)
    if body:
        lines.append("Page text: " + " ".join(body))
    return _cap("\n".join(lines), limit)


def candidates(contact: dict) -> list[str]:
    """URLs to try in order. A domain guessed from the email is tried as www. first: many shops
    only serve their real homepage there."""
    url = site_url(contact)
    if not url:
        return []
    host = urllib.parse.urlsplit(url).hostname or ""
    if (contact.get("website") or "").strip() or host.startswith("www."):
        return [url]
    return [url.replace("://" + host, "://www." + host, 1), url]


def _host(url: str) -> str:
    return (urllib.parse.urlsplit(url).hostname or "").lower().removeprefix("www.")


def social_label(url: str) -> str:
    host = _host(url)
    return next((label for site, label in SOCIAL_SITES.items() if host == site or host.endswith("." + site)), "")


def social_urls(contact: dict) -> list[str]:
    """Profile links from the Socials field and the sheet's other columns; '@handle' is taken as Instagram."""
    texts = [contact.get("socials") or ""]
    try:
        texts += [str(v) for v in json.loads(contact.get("extra") or "{}").values()]
    except (ValueError, AttributeError):
        pass
    own = _host(site_url(contact)) if contact.get("website") else ""
    out = []
    for i, text in enumerate(texts):
        found = URL_IN_TEXT_RE.findall(text)
        for part in re.split(r"[\s,;|]+", text.strip()):
            if m := HANDLE_RE.match(part.rstrip(".")):
                found.append(f"https://www.instagram.com/{m.group(1)}/")
        for u in found:
            u = u.rstrip(".,)")
            if not re.match(r"^https?://", u, re.I):
                u = "https://" + u
            # The Socials cell gives profiles; another column may hold any link about them (a listing, a press page).
            if _host(u) and _host(u) != own and (social_label(u) or i > 0) and u not in out:
                out.append(u)
    return out[:MAX_SOCIALS]


def inner_pages(home_url: str, links: list[str]) -> list[str]:
    """One "about" and one "products" page linked from the homepage, on the same site."""
    out = []
    for rx in INNER_PAGES:
        for href in links:
            u = urllib.parse.urljoin(home_url, href.strip()).split("#")[0]
            if (u.startswith(("http://", "https://")) and _host(u) == _host(home_url)
                    and rx.search(urllib.parse.urlsplit(u).path) and u not in out and u.rstrip("/") != home_url.rstrip("/")):
                out.append(u)
                break
    return out


def read_social(url: str) -> str:
    """Title and description of a public profile. Raises ResearchError behind a login wall."""
    p = parse(fetch_html(url))
    title = " ".join((p.og_title or p.title).split())
    desc = " ".join(p.description.split())
    if not desc:
        raise ResearchError("shows no public description (it may need a login)")
    if LOGIN_WALL_RE.search(title) or LOGIN_WALL_RE.search(desc[:80]):
        raise ResearchError("shows a login page instead of the profile")
    return _cap("\n".join(x for x in (f"Title: {title}" if title else "", f"Description: {desc}") if x), SOCIAL_CHARS)


def _read_website(contact: dict, sections: list[str], read: list[str], errors: list[str]) -> None:
    urls = candidates(contact)
    if not urls:
        errors.append("No website, and the email is a free-mail address")
        return
    error = None
    for url in urls:
        try:
            page = parse(fetch_html(url))
        except ResearchError as e:
            error = error or e
            continue
        text = _summary(page)
        if NOT_CONTENT_RE.search(text):
            error = error or ResearchError("The site showed an error or bot-check page instead of its content")
        elif len(text) >= 40:
            sections.append(f"[Website {url}]\n{text}")
            read.append(url)
            for inner in inner_pages(url, page.links):
                try:
                    more = extract(fetch_html(inner), INNER_CHARS)
                except ResearchError as e:
                    errors.append(f"{inner}: {e}")
                    continue
                if len(more) >= 40 and not NOT_CONTENT_RE.search(more):
                    sections.append(f"[Page {inner}]\n{more}")
                    read.append(inner)
            return
        else:
            error = error or ResearchError("The page had almost no readable text (it may need JavaScript)")
    errors.append(f"Website: {error}")


def research(contact: dict) -> tuple[str, str, list[str]]:
    """(text, the addresses read, what couldn't be read). The text has one block per source.
    Raises ResearchError when nothing at all could be read."""
    sections, read, errors = [], [], []
    _read_website(contact, sections, read, errors)
    for url in social_urls(contact):
        label = social_label(url) or "Link"
        try:
            sections.append(f"[{label} {url}]\n{read_social(url)}")
            read.append(url)
        except ResearchError as e:
            errors.append(f"{label}: {e}")
    if not sections:
        raise ResearchError("; ".join(errors) or "Nothing to read")
    return "\n\n".join(sections), ", ".join(read), errors
