"""Description-driven variation and stock rules for JohnDrop products.

Only values inside an explicit color, size, or model section are variations.
Product specifications are intentionally ignored. Quantities stated beside an
option take precedence; remaining stock is split evenly among unquantified
options, while exhausted options always remain present with stock zero.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Dict, Iterable, List, Mapping


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in text if not unicodedata.combining(c)).lower().strip()


# A section header must name one of these variation dimensions. This deliberately
# does not treat "Características", "Especificações", etc. as variation labels.
_KIND_WORDS = {
    "cor": "cores", "cores": "cores",
    "tamanho": "tamanhos", "tamanhos": "tamanhos",
    "modelo": "modelos", "modelos": "modelos",
}
_HEADER = re.compile(
    r"(?im)^\\s*(?:(?:dispon[ií]ve[il]s?\\s+(?:nas?|nos?|em)\\s*)"
    r"(cores?|tamanhos?|modelos?)|"
    r"(cores?|tamanhos?|modelos?)\\s+dispon[ií]ve[il]s?)\\s*[:\\-]?\\s*(.*)$"
)
_STOP_HEADING = re.compile(
    r"(?i)^\\s*(?:caracter[ií]sticas|especifica[cç][oõ]es|descri[cç][aã]o|"
    r"medidas|dimens[oõ]es|material|composi[cç][aã]o|conte[uú]do|observa[cç][oõ]es)\\b"
)


def _clean_option(value: str) -> str:
    value = re.sub(r"^\\s*[-*•·]+\\s*", "", value).strip()
    # Remove stock annotation without removing the option name itself.
    value = re.sub(
        r"\\s*(?:\\((?:esgotad[oa]s?|sem\\s+estoque|indispon[ií]ve[il]|"
        r"\\d{1,4}\\s*(?:un(?:idades?)?|pcs?|pe[cç]as?)?)\\)|"
        r"(?:[-:]\\s*)?(?:esgotad[oa]s?|sem\\s+estoque|indispon[ií]ve[il])|"
        r"(?:[:\\-–]\\s*\\d{1,4}(?:\\s*(?:un(?:idades?)?|pcs?|pe[cç]as?))?))\\s*$",
        "", value, flags=re.IGNORECASE,
    )
    return value.strip(" .;:-–")


def parse_variation_sections(description: str) -> List[str]:
    """Extract distinct options only from explicit Cor/Tamanho/Modelo sections."""
    if not description:
        return []
    lines = description.replace("\r", "").split("\n")
    options: List[str] = []
    for index, line in enumerate(lines):
        match = _HEADER.match(line)
        if not match:
            continue
        label = (match.group(1) or match.group(2) or "").lower()
        if _KIND_WORDS.get(label) is None:
            continue
        body_lines = [match.group(3) or ""]
        for following in lines[index + 1:]:
            stripped = following.strip()
            if not stripped:
                break
            if _HEADER.match(following) or _STOP_HEADING.match(following):
                break
            # A new colon-terminated heading is not part of this option list.
            if re.match(r"^\\s*[\\wÀ-ÿ /()-]{2,45}:\\s*", following):
                break
            body_lines.append(following)
        for body_line in body_lines:
            # Commas/semicolons separate choices; conjunctions are split only
            # when they join short choices, preserving compound names like
            # "Cinza com preto".
            for part in re.split(r"[,;]", body_line):
                part = _clean_option(part)
                if not part:
                    continue
                if len(part.split()) > 5 or len(part) > 50:
                    continue
                if _fold(part).startswith(("ideal ", "para ", "compatível ", "indicado ", "perfeito ")):
                    continue
                options.append(" ".join(word.capitalize() for word in part.split()))
    result: List[str] = []
    seen = set()
    for option in options:
        key = _fold(option)
        if key and key not in seen:
            seen.add(key)
            result.append(option)
    return result[:30]


_OOS = re.compile(r"\\b(?:esgotad[oa]s?|sem\\s+estoque|indispon[ií]ve[il])\\b", re.I)


def parse_variation_quantities(description: str, variations: Iterable[str]) -> Dict[str, int]:
    """Return only explicitly stated per-option quantities, including exhausted=0."""
    result: Dict[str, int] = {}
    if not description:
        return result
    text = description
    for name in variations:
        escaped = re.escape(name)
        # Match the full option, not a substring of a different option name.
        around = rf"(?<![\\w]){escaped}(?![\\w])"
        before = text[max(0, text.lower().find(name.lower()) - 45):]
        if _OOS.search(before[:80]) and re.search(r"esgotad[oa]s?|sem\\s+estoque|indispon[ií]ve[il]", before, re.I):
            # Confirm the marker belongs to this option (prefix form: "Esgotado: Rosa").
            if re.search(around + r"\\s*(?:\\([^)]*\\))?\\s*(?:[-:]\\s*)?(?:esgotad[oa]s?|sem\\s+estoque|indispon[ií]ve[il])", text, re.I) or re.search(
                r"(?:esgotad[oa]s?|sem\\s+estoque|indispon[ií]ve[il])\\s*[:,-]?\\s*" + around, text, re.I
            ):
                result[name] = 0
                continue
        if re.search(around + r"\\s*(?:\\([^)]*\\))?\\s*(?:[-:]\\s*)?(?:esgotad[oa]s?|sem\\s+estoque|indispon[ií]ve[il])", text, re.I):
            result[name] = 0
            continue
        patterns = (
            around + r"\\s*[:=–-]\\s*(\\d{1,4})\\b",
            around + r"\\s*\\(\\s*(\\d{1,4})\\s*(?:un(?:idades?)?|pcs?|pe[cç]as?)?\\s*\\)",
            r"(\\d{1,4})\\s*(?:un(?:idades?)?|pcs?|pe[cç]as?)?\\s*(?:de\\s+)?" + around,
        )
        for pattern in patterns:
            found = re.search(pattern, text, re.I)
            if found:
                result[name] = int(found.group(1))
                break
    return result


def distribute_stock(total: int, variations: List[str], explicit: Mapping[str, int]) -> Dict[str, int]:
    """Honor explicit quantities; split remaining stock equally among unspecified options."""
    names = list(dict.fromkeys(variations))
    total = max(0, int(total or 0))
    plan: Dict[str, int] = {}
    specified = 0
    unspecified: List[str] = []
    for name in names:
        if name in explicit:
            quantity = max(0, int(explicit[name]))
            plan[name] = quantity
            specified += quantity
        else:
            unspecified.append(name)
    remaining = max(0, total - specified)
    if unspecified:
        base, remainder = divmod(remaining, len(unspecified))
        for index, name in enumerate(unspecified):
            plan[name] = base + (1 if index < remainder else 0)
    return plan


def variation_kind(name: str) -> str:
    """Classify options as size, color, or model; never default unknowns to color."""
    value = _fold(name)
    if re.fullmatch(r"(?:pp|p|m|g|gg|xg|xgg|xs|s|l|xl|xxl|\\d+\\s*(?:ml|l|g|kg|cm|mm)?)", value):
        return "Tamanho"
    colors = (
        "preto", "preta", "branco", "branca", "azul", "verde", "vermelho", "vermelha",
        "amarelo", "amarela", "rosa", "roxo", "roxa", "laranja", "cinza", "marrom",
        "bege", "dourado", "dourada", "prata", "vinho", "lilas", "lilás", "ciano",
        "rose", "rose gold", "azul claro", "azul escuro", "verde claro", "verde escuro",
    )
    if any(value == color or value.startswith(color + " ") or (" com " in value and color in value.split(" com ")) for color in colors):
        return "Cor"
    return "Modelo"
