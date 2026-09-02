"""Regression tests for the two bugs fixed during the JohnDrop→Bling
enrichment investigation:

  1. enrich_worker._is_ready_for_enrichment must wait for IMAGES
     (the "bagagem") to land in Bling — stock alone is NOT a green light.
     Enriching before images arrive corrupts the JohnDrop native sync and
     leaves the Bling product imageless forever.

  2. bling_enrichment._parse_variations must extract compound color
     names ("Cinza com preto", "Vermelho com preto"). The previous 2-word
     cap silently dropped them and the whole list collapsed to []
     (because of the >=2 rule), losing real variations.
"""
import asyncio
import pytest

from enrich_worker import _is_ready_for_enrichment
from bling_enrichment import _parse_variations


# ---------- Worker gate ----------

def _product(images: int = 0, stock: int = 0) -> dict:
    return {
        "midia": {"imagens": {
            "internas": [{"url": f"i{i}.jpg"} for i in range(images)],
            "externas": [],
        }},
        "estoque": {"saldoVirtualTotal": stock},
    }


def test_worker_waits_when_no_images_even_if_stock_present():
    """REGRA do usuário: o robô deve esperar a 'bagagem' (imagens) pousar."""
    ready, reason = asyncio.run(_is_ready_for_enrichment(_product(images=0, stock=89)))
    assert ready is False, "Não pode enriquecer antes das imagens chegarem"
    assert "no_images" in reason


def test_worker_ready_when_at_least_one_image():
    ready, reason = asyncio.run(_is_ready_for_enrichment(_product(images=1, stock=0)))
    assert ready is True
    assert "images=1" in reason


def test_worker_ready_with_many_images():
    ready, _ = asyncio.run(_is_ready_for_enrichment(_product(images=9, stock=120)))
    assert ready is True


def test_worker_waits_with_no_signal():
    ready, _ = asyncio.run(_is_ready_for_enrichment(_product(images=0, stock=0)))
    assert ready is False


# ---------- Variation parser ----------

def test_parse_compound_color_names_three_words():
    """MOU-2820B real description: três cores onde duas são compostas com 'com'."""
    raw = (
        "Mouse Óptico sem Fio PC Notebook com 1600dpi\n"
        "\n"
        "Cores disponíveis: \n"
        " -Preto \n"
        " -Cinza com preto \n"
        " -Vermelho com preto\n"
        "  \n"
        "Medidas da embalagem: 17x5x17cm"
    )
    out = _parse_variations(raw)
    assert "Preto" in out, f"esperava Preto em {out}"
    assert any("Cinza" in v for v in out), f"esperava Cinza... em {out}"
    assert any("Vermelho" in v for v in out), f"esperava Vermelho... em {out}"
    assert len(out) >= 2, f"esperava ao menos 2 variações, achei {out}"


def test_parse_simple_colors():
    raw = "Disponível nas cores: Azul, Verde, Vermelho"
    out = _parse_variations(raw)
    assert set(out) >= {"Azul", "Verde", "Vermelho"}, out


def test_parse_single_color_returns_empty():
    """Uma cor só não conta como variação."""
    raw = "Disponível na cor Preto"
    out = _parse_variations(raw)
    # singular "cor" + 1 item → vazio (gate de plural)
    assert out == []


def test_parse_disclaimer_blocks_variations():
    raw = "Cores disponíveis conforme disponibilidade do estoque: Azul, Verde"
    out = _parse_variations(raw)
    assert out == [], "Disclaimer deve bloquear extração"


def test_parse_filters_descriptive_phrases():
    """Não deve extrair frases descritivas longas."""
    raw = (
        "Cores disponíveis: \n"
        " -Preto \n"
        " -Branco \n"
        " -Ideal Para Setups Temáticos\n"
        "\n"
        "Medidas:"
    )
    out = _parse_variations(raw)
    assert "Preto" in out
    assert "Branco" in out
    assert not any("Ideal" in v for v in out), f"Frase descritiva vazou: {out}"


def test_parse_tamanho_unico_nao_bloqueia_cores():
    """REGRESSÃO: 'Tamanho: Tamanho Único para Adultos' NÃO deve zerar as
    variações de COR (só bloqueia bloco de TAMANHOS)."""
    raw = (
        "Capa de Chuva Impermeável.\n"
        "Tamanho: Tamanho Único para Adultos.\n\n"
        "Cores Disponíveis:\n\n"
        "- Azul\n"
        "- Amarelo\n"
        "- Rosa\n"
        "- Roxo\n"
        "- Preto\n\n"
        "Medidas da embalagem: 3x23x26 cm"
    )
    out = _parse_variations(raw)
    assert set(out) >= {"Azul", "Amarelo", "Rosa", "Roxo", "Preto"}, out


def test_parse_cor_unica_bloqueia_cores():
    raw = "Cor única do produto.\nCores disponíveis: Azul, Verde"
    assert _parse_variations(raw) == []


def test_parse_tamanho_unico_bloqueia_tamanhos():
    raw = "Tamanho Único.\nTamanhos disponíveis: P, M, G"
    assert _parse_variations(raw) == []


def test_parse_singular_cor_com_multiplos_itens():
    """REGRESSÃO W9MAX: 'Disponível na cor:' (singular) com múltiplos itens
    listados abaixo DEVE gerar variações (é uma variação real, não descrição)."""
    raw = (
        "Especificações:\n- Tela 49mm\n\n"
        "Disponível na cor:\n"
        " -Caixa/Bisel PRETO\n"
        " -Caixa/Bisel PRATA\n"
        " -Caixa/Bisel ROSE\n\n"
        "Medidas da embalagem: 23x4x15cm"
    )
    out = _parse_variations(raw)
    assert len(out) == 3, f"esperava 3, achei {out}"
    assert any("Preto" in v.title() or "PRETO" in v.upper() for v in out)


def test_parse_singular_cor_apenas_uma_opcao_ignora():
    """'Disponível na cor Preto' com apenas 1 item continua sendo descritivo."""
    raw = "Disponível na cor Preto"
    assert _parse_variations(raw) == []


# ---------- Regressões 28/07: URSO fan + AL-T12 ----------

def test_parse_blank_lines_between_bullet_items_urso():
    """REGRESSÃO 20220A (Mini Fan URSO): linhas em branco ENTRE as cores
    faziam o parser capturar só a 1ª e colapsar para []."""
    raw = (
        "Mini Ventilador Portátil Recarregável de Mesa Mini Fan URSO\n\n"
        "Marca: Hmaston\n\n"
        "Disponível nas cores:\n\n"
        "-Rosa Claro\n\n-Rosa Escuro\n\n-Pink\n\n-Azul Claro\n\n"
        "-Azul Escuro\n\n-Branco\n\n-Marrom\n\n"
        "Medidas:\n\nL: 10,0 x C: 7,0 x A: 17,5cm\n\nPeso do Produto: 135g"
    )
    out = _parse_variations(raw)
    assert set(out) == {
        "Rosa Claro", "Rosa Escuro", "Pink", "Azul Claro",
        "Azul Escuro", "Branco", "Marrom",
    }, out


def test_parse_esgotado_nao_vira_variacao_alt12():
    """REGRESSÃO AL-T12: 'Rosa - Esgotado.' gerava variação chamada 'Esgotado'."""
    raw = (
        "Fone de Ouvido Intra Auricular AL-T12\n\n"
        "Disponível nas cores:\n"
        "Preto\n"
        "Rosa - Esgotado.\n"
        "Verde - Esgotado.\n"
        "Ciano - Esgotado.\n"
        "Azul - Esgotado.\n"
        "Branco - Esgotado.\n\n"
        "Medidas:\nL: 8,5 x C: 3,0 x A: 17,5cm"
    )
    out = _parse_variations(raw)
    assert set(out) == {"Preto", "Rosa", "Verde", "Ciano", "Azul", "Branco"}, out
    assert not any("esgot" in v.lower() for v in out)


def test_parse_trigger_opcoes_de_cores():
    raw = "Opções de cores:\n- Azul\n- Verde\n\nMedidas: 10cm"
    out = _parse_variations(raw)
    assert set(out) == {"Azul", "Verde"}, out


def test_parse_trigger_secao_cores_header():
    raw = "Fone bluetooth premium.\n\nCores:\n- Preto\n- Branco\n\nGarantia: 90 dias"
    out = _parse_variations(raw)
    assert set(out) == {"Preto", "Branco"}, out


def test_parse_trigger_variacoes():
    raw = "Variações disponíveis:\n- 110v\n- 220v\n\nPeso: 1kg"
    out = _parse_variations(raw)
    assert set(out) == {"110v", "220v"}, out


def test_parse_spec_line_cor_unica_nao_vira_variacao():
    """Linha de spec 'Cor: Preto' (1 item) não é variação."""
    raw = "Mouse gamer.\nCor: Preto\nPeso: 100g"
    assert _parse_variations(raw) == []


def test_parse_sortidas_bloqueia():
    raw = "Cores sortidas enviadas aleatoriamente.\nCores: Azul, Verde, Rosa"
    assert _parse_variations(raw) == []


def test_parse_nao_captura_secao_seguinte():
    """Depois do bloco de cores, 'Medidas' e afins não podem vazar como variação."""
    raw = (
        "Disponível nas cores:\n\n-Branco\n\n-Marrom\n\n"
        "Medidas:\nL: 10,0 x C: 7,0\nPeso: 135g"
    )
    out = _parse_variations(raw)
    assert set(out) == {"Branco", "Marrom"}, out
