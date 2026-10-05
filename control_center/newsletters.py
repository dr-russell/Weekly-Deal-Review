import base64
import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse, urlunparse


class TextAndLinksParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.links: list[dict[str, str]] = []
        self._href: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = dict(attrs)
        if tag.lower() == "a" and attr.get("href"):
            self._href = attr["href"]
        if tag.lower() in {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a":
            self._href = None
        if tag.lower() in {"p", "div", "li", "tr", "h1", "h2", "h3", "h4"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        text = re.sub(r"\s+", " ", data).strip()
        if not text:
            return
        self.parts.append(text)
        if self._href:
            self.links.append({"text": text, "url": clean_url(self._href)})


def html_to_text_and_links(html: str) -> tuple[str, list[dict[str, str]]]:
    parser = TextAndLinksParser()
    parser.feed(html)
    text = "\n".join(line.strip() for line in "".join(parser.parts).splitlines() if line.strip())
    return text, parser.links


def clean_url(url: str) -> str:
    url = unquote(url.strip())
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    for key in ("url", "u", "target", "redirect", "redirect_url"):
        if key in qs and qs[key]:
            candidate = qs[key][0]
            if candidate.startswith("http"):
                return clean_url(candidate)
    filtered_query = []
    for key, values in qs.items():
        lower = key.lower()
        if lower.startswith("utm_") or lower in {"mc_cid", "mc_eid", "emci", "emdi", "ceid"}:
            continue
        for value in values:
            filtered_query.append((key, value))
    query = "&".join(f"{k}={v}" for k, v in filtered_query)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", query, ""))


def decode_message_part(data: str | None) -> str:
    if not data:
        return ""
    padded = data + "=" * (-len(data) % 4)
    try:
        return base64.urlsafe_b64decode(padded.encode()).decode("utf-8", errors="replace")
    except Exception:
        return ""


def extract_message_bodies(payload: dict[str, Any]) -> tuple[str, str]:
    text_parts: list[str] = []
    html_parts: list[str] = []

    def walk(part: dict[str, Any]) -> None:
        mime_type = part.get("mimeType", "")
        body = part.get("body", {})
        data = decode_message_part(body.get("data"))
        if data:
            if mime_type == "text/plain":
                text_parts.append(data)
            elif mime_type == "text/html":
                html_parts.append(data)
        for child in part.get("parts", []) or []:
            walk(child)

    walk(payload)
    return "\n".join(text_parts), "\n".join(html_parts)


def classify_source(subject: str, sender: str, config: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
    haystack = f"{subject} {sender}".lower()
    for key, source_config in config["newsletter_sources"].items():
        for term in source_config["match_terms"]:
            if term.lower() in haystack:
                return key, source_config
    return "unknown", None
