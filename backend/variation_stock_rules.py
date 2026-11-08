"""Description-driven variation and stock rules for JohnDrop products."""
from __future__ import annotations

import re
import unicodedata
from typing import Dict, Iterable, List, Mapping


def _fold(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in normalized if not unicodedata.combining(c)).lower().strip()


_HEADER = re.compile(
    r"^\s*(?:(?:dispon[ií]ve[il]s?\s+(?:nas?|nos?|em)\s*)"
    r"(cores?|tamanhos?|modelos?)|"
    r"(cores?|tamanhos?|modelos?)\s+dispon[ií]ve[il]s?)\s*[:\-]?\s*(.*)$",
    re.IGNORECASE,
)
_STOP_HEADING = re.compile(
    r"^\s*(?:caracter[ií]sticas|especifica[cç][oõ]es|descri[cç][aã]o|"
    r"medidas|dimens[oõ]es|material|composi[cç][aã]o|conte[uú]do|observa[cç][oõ]es)\b",
    re.IGNORECASE,
)


def _clean_option(value: str) -> str:
    value = re.sub(r"^\s*[-*•·]+\s*", "", value).strip()
    value = re.sub(
        r"\s*(?:\((?:esgotad[oa]s?|sem\s+estoque|indispon[ií]ve[il]|"
        r"\d{1,4}\s*(?:un(?:idades?)?|pcs?|pe[cç]as?)?)\)|"
        r"(?:[-:]\s*)?(?:esgotad[oa]s?|sem\s+estoque|indispon[ií]ve[il])|"
        r"(?:[:\-–]\s*\d{1,4}(?:\s*(?:un(?:idades?)?|pcs?|pe[cç]as?))?))\s*$",
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
        body_lines = [match.group(3) or ""]
        for following in lines[index + 1:]:
            if not following.strip() or _HEADER.match(following) or _STOP_HEADING.match(following):
                break
            if re.match(r"^\s*[\wÀ-ÿ /()-]{2,45}:\s*", following):
                break
            body_lines.append(following)
        for body_line in body_lines:
            for part in re.split(r"[,;]", body_line):
                part = _clean_option(part)
                if not part or len(part.split()) > 5 or len(part) > 50:
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


_OOS_AFTER = re.compile(
    r"^\s*(?:\([^)]*\)\s*)?(?:[-:]\s*)?(?:esgotad[oa]s?|sem\s+estoque|indispon[ií]ve[il])\b",
    re.IGNORECASE,
)
_OOS_BEFORE = re.compile(
    r"(?:esgotad[oa]s?|sem\s+estoque|indispon[ií]ve[il])\s*[:,-]?\s*$",
    re.IGNORECASE,
)


def parse_variation_quantities(description: str, variations: Iterable[str]) -> Dict[str, int]:
    """Return explicitly described per-option quantities; exhausted always means zero."""
    result: Dict[str, int] = {}
    if not description:
        return result
    for name in variations:
        escaped = re.escape(name)
        option = rf"(?<![\w]){escaped}(?![\w])"
        # Inspect the option's own line, so a nearby exhausted option cannot
        # accidentally zero another choice.
        matching_lines = [
            line.strip() for line in description.splitlines()
            if re.search(option, line, re.IGNORECASE)
        ]
        for line in matching_lines:
            found = re.search(option, line, re.IGNORECASE)
            if not found:
                continue
            after = line[found.end():]
            before = line[:found.start()]
            if _OOS_AFTER.search(after) or _OOS_BEFORE.search(before):
                result[name] = 0
                break
            patterns = (
                option + r"\s*[:=–-]\s*(\d{1,4})\b",
                option + r"\s*\(\s*(\d{1,4})\s*(?:un(?:idades?)?|pcs?|peças?)?\s*\)",
                r"(\d{1,4})\s*(?:un(?:idades?)?|pcs?|peças?)?\s*(?:de\s+)?" + option,
            )
            for pattern in patterns:
                quantity = re.search(pattern, line, re.IGNORECASE)
                if quantity:
                    result[name] = int(quantity.group(1))
                    break
            if name in result:
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
    """Classify options as size, color, or model; unknowns are models, not colors."""
    value = _fold(name)
    if re.fullmatch(r"(?:pp|p|m|g|gg|xg|xgg|xs|s|l|xl|xxl|\d+\s*(?:ml|l|g|kg|cm|mm)?)", value):
        return "Tamanho"
    colors = (
        "preto", "preta", "branco", "branca", "azul", "verde", "vermelho", "vermelha",
        "amarelo", "amarela", "rosa", "roxo", "roxa", "laranja", "cinza", "marrom",
        "bege", "dourado", "dourada", "prata", "vinho", "lilas", "ciano", "rose",
        "azul claro", "azul escuro", "verde claro", "verde escuro",
    )
    if any(value == color or value.startswith(color + " ") or (" com " in value and color in value.split(" com ")) for color in colors):
        return "Cor"
    return "Modelo"
