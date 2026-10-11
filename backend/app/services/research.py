"""Website research: a short plain-text excerpt of a partner's own site, so the
first email can mention what they actually sell or do, not just their category.

The site comes from the contact's Website field, or failing that from their email
domain (unless it's a free-mail provider). Only public http(s) addresses are
fetched, every redirect hop is checked too, and the download is size-capped. The
excerpt is the partner's own marketing: the prompt tells Claude to use it only to
be specific and never to repeat its claims, and a person still approves the draft.
"""
import ipaddress
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
        self.headings: list[str] = []
        self.text: list[str] = []
        self._skip = 0
        self._in_title = False
        self._heading = None

    def handle_starttag(self, tag, attrs):
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


def extract(html: str) -> str:
    """Title, meta description, headings and body text, as one short block of plain text."""
    p = _Extractor()
    try:
        p.feed(html)
        p.close()
    except Exception:  # malformed pages: keep whatever was parsed
        pass
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
    out = "\n".join(lines)
    return out[:MAX_CHARS].rsplit(" ", 1)[0] + " …" if len(out) > MAX_CHARS else out


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


def research(contact: dict) -> tuple[str, str]:
    """(site_text, url). Raises ResearchError when there's no site or it can't be read."""
    urls = candidates(contact)
    if not urls:
        raise ResearchError("No website, and the email is a free-mail address")
    error = None
    for url in urls:
        try:
            text = extract(fetch_html(url))
        except ResearchError as e:
            error = error or e
            continue
        if NOT_CONTENT_RE.search(text):
            error = error or ResearchError("The site showed an error or bot-check page instead of its content")
        elif len(text) >= 40:
            return text, url
        else:
            error = error or ResearchError("The page had almost no readable text (it may need JavaScript)")
    raise error
