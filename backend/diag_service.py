"""Diagnostic endpoints — no LLM cost, just inspect parser/state."""
from fastapi import APIRouter
from pydantic import BaseModel

from bling_enrichment import _parse_variations, _parse_variation_quantities

router = APIRouter(prefix="/diag", tags=["diag"])


class DescriptionPayload(BaseModel):
    description: str


@router.post("/parse-description")
async def parse_description(payload: DescriptionPayload) -> dict:
    """Cole a descrição do produto e veja EXATAMENTE o que o robô interpretaria.
    Não chama LLM — só roda regex local. Custo: zero."""
    variations = _parse_variations(payload.description)
    quantities = _parse_variation_quantities(payload.description, variations)
    return {
        "variacoes_detectadas": variations,
        "vai_criar_variacoes": len(variations) > 0,
        "quantidades_explicitas": quantities,
        "regra_aplicada": (
            "Sem variações — produto simples (mantém estoque integrado JohnDrop→Bling)"
            if not variations else
            "Quantidades específicas detectadas — usa valores da descrição"
            if quantities else
            "Sem quantidades específicas — vai dividir estoque do pai igualmente"
        ),
    }
