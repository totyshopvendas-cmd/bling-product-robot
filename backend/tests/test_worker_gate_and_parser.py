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

from enrich_worker import (
    _is_ready_for_enrichment,
    _is_totyshop_bling_integration,
    _source_text_for_enrichment,
)
from bling_enrichment import (
    _extract_johndrop_id_from_observations,
    _is_sortido,
    _parse_variations,
    _parse_variation_quantities,
)
from bling_variations import _build_stock_plan


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


def test_manual_johndrop_monitor_accepts_only_totyshop_bling():
    assert _is_totyshop_bling_integration({"integracao": "TotyShop-Bling"}) is True
    assert _is_totyshop_bling_integration({"canal": "TotyShop Bling"}) is True
    assert _is_totyshop_bling_integration({"integracao": "TotyShop-Shopee"}) is False
    assert _is_totyshop_bling_integration({"integracao": "Bling"}) is False


def test_manual_johndrop_uses_original_bling_description():
    """Manual items have no local raw record; the synced Bling text is the source."""
    title, description = _source_text_for_enrichment(
        {"raw_title": "Lanterna manual", "raw_description": ""},
        {"nome": "Outro título", "descricao": "Cor:\n- Preto\n- Azul"},
    )
    assert title == "Lanterna manual"
    assert description == "Cor:\n- Preto\n- Azul"


def test_extracts_johndrop_supplier_id_from_observations():
    product = {"observacoes": "Integração Jonhdrop id : 209544"}
    assert _extract_johndrop_id_from_observations(product) == "209544"


def test_extracts_johndrop_supplier_id_from_alternate_observation_shape():
    product = {"observacaoInterna": {"texto": "Integração JohnDrop código # 99881"}}
    assert _extract_johndrop_id_from_observations(product) == "99881"


def test_missing_johndrop_observation_does_not_invent_supplier_id():
    assert _extract_johndrop_id_from_observations({"observacoes": "Cadastro interno"}) is None


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


def test_inline_colors_stop_before_trimpro_characteristics():
    """The real TrimPro layout puts features immediately after its inline colors."""
    raw = (
        "Características:\n"
        "Kit com 10 unidades\n"
        "Modelo: Capacete de Segurança EPI\n"
        "Material: Plástico\n"
        "Cores disponíveis: Amarelo, Azul, Branco, Preto, Verde e Vermelho\n"
        "Argola metálica\n"
        "Leve e prático\n"
        "Excelente opção para brindes e ações promocionais\n"
        "Medidas aproximadas de cada chaveiro"
    )
    assert _parse_variations(raw) == [
        "Amarelo", "Azul", "Branco", "Preto", "Verde", "Vermelho",
    ]


def test_explicit_cor_section_without_disponivel_creates_variations():
    """Supplier sections headed only by 'Cor:' are selectable variations."""
    raw = (
        "Lanterna LED Super Brilhante\n\n"
        "**Características Técnicas:**\n"
        "- Material: Liga de alumínio resistente.\n"
        "- Tipo de Iluminação: LED Super Brilhante.\n\n"
        "**Cor:**\n"
        "- Preto\n"
        "- Vermelho\n"
        "- Dourado\n"
        "- Azul\n\n"
        "**Medidas da embalagem:** 5 x 5 x 12 cm"
    )
    assert _parse_variations(raw) == ["Preto", "Vermelho", "Dourado", "Azul"]


def test_explicit_tamanho_section_without_disponivel_creates_variations():
    raw = "Tamanho:\n- P\n- M\n- G\n\nMedidas: consulte a tabela"
    assert _parse_variations(raw) == ["P", "M", "G"]


def test_markdown_esgotado_creates_variation_with_zero_stock():
    """The supplier's Markdown list must keep the sold-out option as a child."""
    raw = (
        "**Disponível nas cores:**  \n"
        "- Branco  \n"
        "- Cinza Escuro (ESGOTADO)"
    )
    variations = _parse_variations(raw)
    quantities = _parse_variation_quantities(raw, variations)

    assert variations == ["Branco", "Cinza Escuro"]
    assert quantities == {"Cinza Escuro": 0}
    # With 12 units in the parent, the available option gets all 12 while the
    # sold-out option still exists and is explicitly assigned zero.
    assert _build_stock_plan(12, variations, quantities) == {
        "Branco": 12,
        "Cinza Escuro": 0,
    }


def test_stock_plan_subtracts_explicit_amount_before_split():
    """Explicit quantities and sold-out children cannot inflate total stock."""
    plan = _build_stock_plan(
        20,
        ["Branco", "Cinza", "Preto"],
        {"Cinza": 0, "Preto": 8},
    )
    assert plan == {"Branco": 12, "Cinza": 0, "Preto": 8}
    assert sum(plan.values()) == 20


def test_create_variations_applies_zero_to_esgotado_child(monkeypatch):
    """The initial creation flow must send an explicit zero to Bling."""
    import bling_variations

    class FakeResponse:
        def __init__(self, data):
            self.status_code = 200
            self._data = data
            self.text = ""

        def json(self):
            return self._data

    async def fake_request(method, path, **_kwargs):
        if method == "PATCH":
            return FakeResponse({"data": {"variations": {"saved": [
                {"id": 101, "nomeVariacao": "Cor:Branco"},
                {"id": 102, "nomeVariacao": "Cor:Cinza Escuro"},
            ]}}})
        return FakeResponse({"data": {"midia": {"imagens": {"internas": []}}}})

    stocks = []

    async def capture_stock(ids, quantity):
        stocks.append((ids[0], quantity))

    async def noop(*_args, **_kwargs):
        return None

    async def default_deposit():
        return 1

    monkeypatch.setattr(bling_variations.bling_service, "bling_request", fake_request)
    monkeypatch.setattr(bling_variations, "_set_children_stock", capture_stock)
    monkeypatch.setattr(bling_variations, "_get_default_deposito_id", default_deposit)
    monkeypatch.setattr(bling_variations, "add_log", noop)

    result = asyncio.run(bling_variations.create_variations(
        99,
        {"nome": "Produto", "codigo": "SKU-1", "preco": 10},
        ["Branco", "Cinza Escuro"],
        total_stock=12,
        explicit_quantities={"Cinza Escuro": 0},
    ))

    assert result["created"] == 2
    assert dict(stocks) == {101: 12, 102: 0}


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


def test_markdown_random_colors_do_not_create_variations():
    """A list of colors is not selectable when the supplier sends one at random."""
    raw = (
        "**Disponível nas cores:**  \n"
        "**- Preto**  \n"
        "**- Rosa**  \n"
        "**- Branco**  \n"
        "**- Azul**  \n"
        "**(As cores são enviadas aleatoriamente conforme disponibilidade em estoque)**"
    )
    assert _is_sortido(raw) is True
    assert _parse_variations(raw) == []


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


def test_characteristics_do_not_become_trimpro_variations():
    """A key/value feature block is not a selectable color/size/model list."""
    raw = (
        "Características do produto:\n"
        "Cor: Preto\n"
        "Material: EVA\n"
        "Tamanho: 30 x 40 cm\n"
        "Medidas: 30 x 40 cm"
    )
    assert _parse_variations(raw) == []


def test_explicit_options_ignore_following_trimpro_characteristics():
    """Real options remain valid; only the later Material/Medidas rows are ignored."""
    raw = (
        "Cor:\n"
        "- Preto\n"
        "- Azul\n"
        "Material: EVA\n"
        "Medidas: 30 x 40 cm"
    )
    assert _parse_variations(raw) == ["Preto", "Azul"]


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
