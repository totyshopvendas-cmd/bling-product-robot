"""Title cleaner engine — deterministic regex + optional LLM fallback.

Rules (from problem statement):
1. No brand names (XLS, Kapbom, Inova, Altomex, Eletromex, ...)
2. No EAN codes (13-digit numbers)
3. Code (e.g. JZ-USBD, KA-6070, B125, A-P18) must be the LAST token
4. No special characters except hyphen (-)
5. Max 60 characters
"""
import re
from typing import List, Tuple, Optional

# Brands to strip entirely (case-insensitive whole-word)
BLOCKED_BRANDS = [
    "XLS", "Kapbom", "Inova", "Altomex", "Eletromex", "Hayamax",
    "JONH", "JONHDROP", "JONH DROP", "JonhDrop", "Jonh Drop",
    "Variedades", "Jonh Variedades",
    "Generic", "Generico", "Genérico",
]

# Filler / marketing words to remove
FILLER_WORDS = [
    "alta qualidade", "altíssima qualidade", "o melhor", "melhor preço",
    "promocional", "promoção", "oferta", "frete grátis",
    "envio rápido", "pronta entrega", "novo lacrado", "novo modelo",
    "original lacrado", "lacrado", "garantia", "importado",
    "100% original", "100 original", "100%",
    "kit completo", "super promoção",
]

# Stock warnings from supplier
STOCK_WARNINGS = [
    r"\(últimas\s+\d+\s+unidades?\)",
    r"\(restam\s+\d+\)",
    r"estoque\s+baixo",
    r"poucas\s+unidades",
]

# Allowed code patterns — must look like a product code:
# - Letters+digits possibly with hyphens. E.g. JZ-USBD, KA-6070, B125, A-P18, KA-S079, KA-1369
CODE_REGEX = re.compile(r"\b([A-Z]{1,5}[\-/]?[A-Z]?\d{1,5}[\-/]?[A-Z0-9]{0,6})\b")

# EAN: 8, 12, 13 or 14 digit sequences
EAN_REGEX = re.compile(r"\b\d{8,14}\b")

# Strip parentheses & their content (e.g. "(4X AAA)")
PAREN_REGEX = re.compile(r"\([^)]*\)")

# Allowed characters: letters, digits, spaces, and hyphen. Strip everything else.
ALLOWED_REGEX = re.compile(r"[^\w\s\-áéíóúâêîôûãõàèìòùçÁÉÍÓÚÂÊÎÔÛÃÕÀÈÌÒÙÇ]+", re.UNICODE)

# Words too generic that should be deduped
REDUNDANT_PAIRS = [
    ("Touch Screen", "Touch"),
]

MAX_LEN = 60


def _find_codes(text: str) -> List[str]:
    """Find all candidate product codes in text. Returns unique preserving order."""
    found = []
    for m in CODE_REGEX.finditer(text):
        code = m.group(1)
        # Filter out short matches that are just numbers or single letters
        if len(code) < 3:
            continue
        # Must contain at least one letter AND one digit OR a hyphen
        has_letter = any(c.isalpha() for c in code)
        has_digit = any(c.isdigit() for c in code)
        if not (has_letter and (has_digit or "-" in code)):
            continue
        if code not in found:
            found.append(code)
    return found


def _strip_brands(text: str) -> Tuple[str, List[str]]:
    removed = []
    for brand in BLOCKED_BRANDS:
        pattern = re.compile(rf"\b{re.escape(brand)}\b", re.IGNORECASE)
        if pattern.search(text):
            removed.append(brand)
        text = pattern.sub(" ", text)
    return text, removed


def _strip_fillers(text: str) -> Tuple[str, List[str]]:
    removed = []
    for w in FILLER_WORDS:
        pattern = re.compile(re.escape(w), re.IGNORECASE)
        if pattern.search(text):
            removed.append(w)
        text = pattern.sub(" ", text)
    for pat in STOCK_WARNINGS:
        text = re.sub(pat, " ", text, flags=re.IGNORECASE)
    return text, removed


def _strip_eans(text: str) -> Tuple[str, List[str]]:
    removed = EAN_REGEX.findall(text)
    text = EAN_REGEX.sub(" ", text)
    return text, removed


def _normalize_chars(text: str) -> str:
    # Remove parentheses content
    text = PAREN_REGEX.sub(" ", text)
    # Replace slashes with space (connectors removed)
    text = re.sub(r"[/&|+]", " ", text)
    # Strip everything except allowed characters
    text = ALLOWED_REGEX.sub(" ", text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _smart_truncate(words: List[str], max_len: int, suffix: str) -> str:
    """Build string from words ensuring final + suffix <= max_len."""
    if suffix:
        budget = max_len - len(suffix) - 1  # account for space
    else:
        budget = max_len
    out = ""
    for w in words:
        add = (" " if out else "") + w
        if len(out) + len(add) > budget:
            break
        out += add
    out = out.strip()
    if suffix:
        return f"{out} {suffix}".strip()
    return out


CONNECTORS = {"para", "e", "de", "da", "do", "com", "em", "ao", "à", "ou"}


def _extract_trailing_codes(text: str) -> Tuple[str, List[str]]:
    """Detect 1 or 2 trailing tokens that look like product codes and return them.
    Returns (text_without_trailing, [codes_in_order])."""
    tokens = text.strip().split()
    trailing: List[str] = []
    while tokens:
        last = tokens[-1].strip(".,;:!?")
        # Skip slash-only / paren-only tokens by cleaning them first
        candidate = re.sub(r"[^A-Za-z0-9\-]", "", last)
        if not candidate:
            tokens.pop()
            continue
        if len(candidate) < 3:
            break
        has_letter = any(c.isalpha() for c in candidate)
        has_digit = any(c.isdigit() for c in candidate)
        if has_letter and (has_digit or "-" in candidate):
            trailing.insert(0, candidate)
            tokens.pop()
            if len(trailing) >= 2:
                break
        else:
            break
    return " ".join(tokens), trailing


def clean_title(raw: str, preferred_code: Optional[str] = None) -> dict:
    """Clean a product title following all rules. Returns dict with details."""
    original = raw or ""
    text = original

    removed_terms: List[str] = []

    # 1. Strip EANs first (before code detection so 13-digit numbers don't get confused with codes)
    text, eans = _strip_eans(text)
    if eans:
        removed_terms.extend([f"EAN:{e}" for e in eans])

    # 2. Strip brands
    text, brands = _strip_brands(text)
    removed_terms.extend(brands)

    # 3. Strip filler words / stock warnings
    text, fillers = _strip_fillers(text)
    removed_terms.extend(fillers)

    # 4. Normalize chars (keep hyphen only)
    text = _normalize_chars(text)

    # 5. Detect TRAILING codes (1 or 2 tokens at the end that look like codes)
    body, trailing_codes = _extract_trailing_codes(text)

    if preferred_code:
        # Force preferred_code to be the trailing suffix
        trailing_codes = [preferred_code]
        # Remove any occurrence of preferred_code from body
        body = re.sub(rf"\b{re.escape(preferred_code)}\b", " ", body, flags=re.IGNORECASE)

    # 6. Remove leftover inline codes from body (anything still matching code pattern)
    body_codes = _find_codes(body)
    for c in body_codes:
        body = re.sub(rf"\b{re.escape(c)}\b", " ", body, flags=re.IGNORECASE)
    body = re.sub(r"\s+", " ", body).strip()

    # 7. Remove connector words and dedupe
    seen = set()
    deduped = []
    for w in body.split():
        wl = w.lower()
        if wl in CONNECTORS:
            continue
        if wl in seen:
            continue
        seen.add(wl)
        deduped.append(w)

    # 8. Build suffix from trailing codes
    suffix = " ".join(trailing_codes)
    chosen_code = trailing_codes[-1] if trailing_codes else None

    # 9. Smart truncate to MAX_LEN, ensuring suffix fits at end
    cleaned = _smart_truncate(deduped, MAX_LEN, suffix)

    # Safety: hard truncate if still too long (very unlikely)
    if len(cleaned) > MAX_LEN:
        cleaned = cleaned[:MAX_LEN].rstrip()

    return {
        "raw": original,
        "cleaned": cleaned,
        "length": len(cleaned),
        "removed_terms": removed_terms,
        "code_used": chosen_code,
        "method": "regex",
    }
