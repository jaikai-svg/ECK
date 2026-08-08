from __future__ import annotations

import hashlib
import re
from collections import Counter
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_tracking_parameters = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ref",
    "ref_src",
}


def canonicalize_url(url: str) -> str:
    parsed = urlsplit(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("A canonical research URL must use public HTTP(S).")
    host = parsed.hostname.casefold().rstrip(".")
    port = parsed.port
    if port and not (
        (parsed.scheme == "http" and port == 80)
        or (parsed.scheme == "https" and port == 443)
    ):
        host = f"{host}:{port}"
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.casefold().startswith("utm_")
        and key.casefold() not in _tracking_parameters
    ]
    return urlunsplit((parsed.scheme.casefold(), host, path, urlencode(sorted(query)), ""))


def source_domain(url: str) -> str:
    return (urlsplit(url).hostname or "").casefold().rstrip(".")


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalize_document_text(value: str) -> str:
    paragraphs = []
    for line in value.replace("\r", "\n").split("\n"):
        cleaned = " ".join(line.split())
        if cleaned:
            paragraphs.append(cleaned)
    return "\n".join(paragraphs)


def simhash64(value: str) -> str:
    tokens = re.findall(r"[a-z0-9]{3,}|[\u4e00-\u9fff]{2,}", value.casefold())
    counts = Counter(tokens)
    if not counts:
        return "0" * 16
    vector = [0] * 64
    for token, weight in counts.items():
        digest = int.from_bytes(
            hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest(),
            "big",
        )
        for bit in range(64):
            vector[bit] += weight if digest & (1 << bit) else -weight
    fingerprint = 0
    for bit, score in enumerate(vector):
        if score >= 0:
            fingerprint |= 1 << bit
    return f"{fingerprint:016x}"


def simhash_distance(first: str, second: str) -> int:
    return (int(first, 16) ^ int(second, 16)).bit_count()
