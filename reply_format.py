# shorten agent replies for glasses TTS — especially price lookups.

import re

_PRICE = re.compile(r"\$[\d,]+(?:\.\d{2})?")
_PRICE_INTENT = re.compile(
    r"\b(price|cost|how much|cheapest|find.*amazon|search.*amazon|score for)\b", re.I
)


def is_price_task(query: str) -> bool:
    return bool(_PRICE_INTENT.search(query or ""))


def _target_brand(query: str) -> str | None:
    """Brand/product from glasses POV context embedded in the enriched prompt."""
    for pat in (r"- Brand:\s*(.+)", r"- Product:\s*(.+)", r"Suggested search:\s*(.+)"):
        m = re.search(pat, query, re.I)
        if m:
            return m.group(1).strip()
    return None


def _brand_matches(query: str, text: str) -> bool:
    target = _target_brand(query)
    if not target or not text:
        return True
    # normalize so "ScarAway" / "Scar Away" compare as one token
    brand = re.sub(r"[^a-z0-9]", "", target.lower())
    hay = re.sub(r"[^a-z0-9]", "", text.lower())
    if len(brand) >= 4 and brand in hay:
        return True
    # multi-word brands: require a distinctive word (4+ chars), not generic product terms
    skip = {"scar", "gel", "silicone", "cream", "advanced", "reduction", "pack", "ounce", "oz"}
    words = [w.lower() for w in re.findall(r"[A-Za-z0-9]+", target) if len(w) >= 4 and w.lower() not in skip]
    if not words:
        return True
    return any(w in hay for w in words)


def price_from_text(text: str) -> str | None:
    m = _PRICE.search(text or "")
    return m.group() if m else None


def compact_glasses_reply(query: str, answer: str) -> str:
    text = (answer or "").strip()
    if not text:
        return text

    if is_price_task(query):
        price = price_from_text(text)
        if price:
            if not _brand_matches(query, text):
                return f"{price}, different brand"
            return price

    first_line = text.split("\n")[0].strip()
    if len(first_line) <= 80:
        return first_line
    return first_line[:77].rsplit(" ", 1)[0] + "…"
