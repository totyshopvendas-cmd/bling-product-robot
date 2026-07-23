"""Unit tests for the semantic LLM matching fix.

Root cause fixed: the LLM returns a reflection paragraph BEFORE the final
NUMERO|CONFIANCA answer. Using `re.match` (start-of-string) failed to parse
this — we now use `re.findall` and take the LAST match.
"""
import sys
import os
import asyncio
import re
from unittest.mock import patch, AsyncMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import category_mapping as cm  # noqa: E402


def test_regex_findall_last_extracts_answer_from_reasoning():
    """LLM response with reasoning + final answer 'NUMERO|CONF' should parse."""
    reply = (
        "REFLEXÃO:\n'Barbeador' é um produto de cuidados pessoais...\n\n"
        "Analisando:\n1. Camisetas - vestuário (não)\n"
        "6. Beleza e Cuidados Pessoais - APLICÁVEL\n\n**RESPOSTA:**\n\n6|0.92"
    )
    matches = re.findall(r"(\d+)\s*\|\s*([\d.]+)", reply.strip())
    assert matches, "expected at least one match"
    num, conf = matches[-1]
    assert num == "6"
    assert float(conf) == 0.92


def test_regex_findall_short_answer():
    """Short direct answer also parses."""
    reply = "5|0.85"
    matches = re.findall(r"(\d+)\s*\|\s*([\d.]+)", reply.strip())
    assert matches == [("5", "0.85")]


def test_regex_no_match_returns_empty():
    """Non-formatted response returns empty list."""
    reply = "Não sei responder."
    matches = re.findall(r"(\d+)\s*\|\s*([\d.]+)", reply.strip())
    assert matches == []


def test_llm_pick_semantic_barbeador_to_beleza():
    """Barbeador should map to Beleza, NOT to Eletrônicos (semantic, not tokens)."""
    async def _run():
        candidates_raw = [
            {"codigo": "e1", "descricao": "Eletrônicos e Áudio"},
            {"codigo": "e2", "descricao": "Informática"},
            {"codigo": "b1", "descricao": "Beleza e Cuidados Pessoais"},
            {"codigo": "b2", "descricao": "Cabelo e Barba"},
        ]
        # Mock LLM to return reasoning + '3|0.92' (index 3 = Beleza)
        mock_reply = (
            "REFLEXÃO: Barbeador é produto de cuidados pessoais.\n\n"
            "RESPOSTA: 3|0.92"
        )

        class FakeChat:
            def with_model(self, *_a, **_kw): return self
            async def send_message(self, *_a, **_kw): return mock_reply

        with patch.dict(os.environ, {"EMERGENT_LLM_KEY": "test-key"}), \
             patch("emergentintegrations.llm.chat.LlmChat", return_value=FakeChat()):
            result = await cm._llm_pick_from_existing(
                "Barbeador", "Shopee", candidates_raw,
            )
        assert result is not None
        assert result["descricao"] == "Beleza e Cuidados Pessoais"
        assert result["codigo"] == "b1"
        assert result["confidence"] == 0.92
        assert result["reason"] == "llm semantic"

    asyncio.run(_run())


def test_llm_pick_low_confidence_flagged_not_applied():
    """Confidence < 0.5 should return the pick but with a warning reason."""
    async def _run():
        candidates_raw = [
            {"codigo": "x1", "descricao": "Categoria A"},
            {"codigo": "x2", "descricao": "Categoria B"},
        ]
        mock_reply = "1|0.35"  # baixa confiança

        class FakeChat:
            def with_model(self, *_a, **_kw): return self
            async def send_message(self, *_a, **_kw): return mock_reply

        with patch.dict(os.environ, {"EMERGENT_LLM_KEY": "test-key"}), \
             patch("emergentintegrations.llm.chat.LlmChat", return_value=FakeChat()):
            result = await cm._llm_pick_from_existing(
                "Categoria Estranha", "Shopee", candidates_raw,
            )
        assert result is not None
        assert result["confidence"] == 0.35
        assert "baixa conf" in result["reason"]

    asyncio.run(_run())


def test_llm_pick_zero_response_returns_none():
    """LLM answer '0|...' means 'nenhum é adequado' → returns None."""
    async def _run():
        candidates_raw = [{"codigo": "x1", "descricao": "Cat A"}]
        mock_reply = "0|0.0"

        class FakeChat:
            def with_model(self, *_a, **_kw): return self
            async def send_message(self, *_a, **_kw): return mock_reply

        with patch.dict(os.environ, {"EMERGENT_LLM_KEY": "test-key"}), \
             patch("emergentintegrations.llm.chat.LlmChat", return_value=FakeChat()):
            result = await cm._llm_pick_from_existing(
                "Something weird", "Shopee", candidates_raw,
            )
        # idx = 0 - 1 = -1 → returns None
        assert result is None

    asyncio.run(_run())
