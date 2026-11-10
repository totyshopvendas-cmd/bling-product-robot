"""Geração das descrições do Bling para produtos lidos do Dropper.

Regras iguais às do robô JohnDrop (mesma *lógica*, sem estado/banco/logs
compartilhados — este módulo só chama as funções de texto do
``bling_enrichment``):

* **Descrição Curta** — 3 a 5 parágrafos, texto limpo (sem emoji).
* **Descrição Complementar** — 8 bullets + bloco ``<b>Ficha Técnica:</b>``.

Se a IA estiver indisponível (chave errada, saldo esgotado), NADA é inventado:
cai para um resumo montado a partir da própria descrição do Dropper e o
relatório avisa que aquele texto precisa de revisão.
"""
from __future__ import annotations

import html
import re
from typing import List, Tuple

#: marcador do bloco de ficha técnica dentro da Descrição Complementar
FICHA_HEADER = "<b>Ficha Técnica:</b>"

_TAGS_RE = re.compile(r"<[^>]+>")
_SPACES_RE = re.compile(r"[ \t]+")


def _clean_lines(text: str) -> List[str]:
    """Linhas da descrição do fornecedor, sem HTML e sem vazias."""
    text = html.unescape(_TAGS_RE.sub(" ", text or ""))
    out: List[str] = []
    for raw in text.splitlines():
        line = _SPACES_RE.sub(" ", raw).strip(" -–—•·\t")
        if line:
            out.append(line)
    return out


def fallback_short(title: str, description: str, max_chars: int = 900) -> str:
    """Descrição curta sem IA: aproveita a descrição do fornecedor.

    Uma linha por linha (o ``short_to_html`` transforma em ``<br>``), porque
    juntar tudo com espaço vira um texto corrido ilegível quando a descrição
    tem itens de especificação.
    """
    lines = _clean_lines(description)
    if not lines:
        return ""
    # ignora o cabeçalho que só repete o título
    corpo = [ln for ln in lines if ln.upper() != (title or "").upper()] or lines
    chosen: List[str] = []
    total = 0
    for line in corpo:
        espaco = max_chars - total
        if espaco <= 0:
            break
        if len(line) <= espaco:
            chosen.append(line)
            total += len(line) + 1
            continue
        # linha maior que o que resta: corta no fim de uma frase, se der
        corte = line[:espaco]
        ponto = corte.rfind(". ")
        if ponto >= 40:
            corte = corte[: ponto + 1]
        chosen.append(corte.rstrip())
        break
    return "\n".join(c for c in chosen if c).strip()


def compose_complementary(bullets: List[str], ficha: List[str]) -> str:
    """Monta a Descrição Complementar HTML: 8 bullets + Ficha Técnica."""
    parts: List[str] = []
    for b in bullets:
        b = (b or "").strip()
        if not b:
            continue
        if not b.startswith("-"):
            b = "- " + b
        parts.append(b)
    ficha = [f.strip() for f in (ficha or []) if f and f.strip()]
    if ficha:
        parts.append(FICHA_HEADER)
        parts.extend(ficha)
    return "<br>".join(parts)


def short_to_html(text: str) -> str:
    """O Bling renderiza HTML nos campos de descrição."""
    if not text:
        return ""
    return text.replace("\n\n", "<br><br>").replace("\n", "<br>")


def fallback_complementary(description: str, limit: int = 8) -> str:
    """Sem IA: usa as linhas de especificação do fornecedor como bullets."""
    bullets: List[str] = []
    for line in _clean_lines(description):
        if re.match(r"^[-•·]\s*", line) or ":" in line:
            if 3 <= len(line) <= 150:
                bullets.append("- " + line.lstrip("-•· ").strip())
        if len(bullets) >= limit:
            break
    if not bullets:
        return ""
    return compose_complementary(bullets[:limit], [])


async def build_descriptions(title: str, description: str,
                             use_ai: bool = True) -> Tuple[str, str, str]:
    """Devolve ``(descricao_curta, descricao_complementar, origem)``.

    ``origem`` é ``"ia"``, ``"fornecedor"`` (fallback) ou ``"vazio"``.
    Nunca levanta exceção: o robô não pode parar por causa de descrição.
    """
    if use_ai:
        try:
            import bling_enrichment as be

            short, bullets = await _ai_texts(be, title, description)
            ficha: List[str] = []
            try:
                attrs = await be._extract_attributes(title, description)
                ficha = be._attributes_to_bullets(attrs) if attrs else []
            except Exception:
                ficha = []
            if short or bullets:
                curta = short_to_html(short) if short else ""
                compl = compose_complementary(bullets, ficha)
                return curta, compl, "ia"
        except Exception:
            pass

    curta = fallback_short(title, description)
    compl = fallback_complementary(description)
    if curta or compl:
        return short_to_html(curta), compl, "fornecedor"
    return "", "", "vazio"


async def _ai_texts(be, title: str, description: str) -> Tuple[str, List[str]]:
    """Chama as duas gerações de texto do bling_enrichment em paralelo."""
    import asyncio

    results = await asyncio.gather(
        be.generate_short_description(title, description),
        be.generate_bullet_points(title, description),
        return_exceptions=True,
    )
    short = results[0] if isinstance(results[0], str) else ""
    bullets = results[1] if isinstance(results[1], list) else []
    return short, bullets
