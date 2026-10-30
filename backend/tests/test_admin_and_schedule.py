"""Testes unitários: login (cookie assinado), agendamento diário e banco local."""
import asyncio
import time
from datetime import datetime

import pytest


# ------------------------------------------------------------------ login ---
def test_auth_sign_and_verify(monkeypatch):
    import admin_service as a
    monkeypatch.setenv("APP_PASSWORD", "segredo")
    monkeypatch.setenv("APP_SECRET", "chave")
    assert a.auth_required()
    tok = a._sign(int(time.time()) + 60)
    assert a._valid(tok)
    assert not a._valid(tok[:-1] + ("0" if tok[-1] != "0" else "1"))  # adulterado
    assert not a._valid(a._sign(int(time.time()) - 1))  # expirado
    assert not a._valid(None) and not a._valid("lixo")
    # trocar a senha invalida as sessões antigas
    monkeypatch.setenv("APP_PASSWORD", "outra")
    assert not a._valid(tok)


def test_auth_disabled_without_password(monkeypatch):
    import admin_service as a
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    assert not a.auth_required()


# ------------------------------------------------------------ agendamento ---
def test_due_today():
    from server import due_today
    d = datetime(2026, 10, 30, 7, 29)
    assert not due_today(d, "07:30", None)            # ainda não chegou a hora
    assert due_today(d.replace(minute=30), "07:30", None)
    assert due_today(d.replace(hour=23), "07:30", "2026-10-29")  # PC ligou tarde
    assert not due_today(d.replace(hour=23), "07:30", "2026-10-30")  # já rodou hoje


# ------------------------------------------------------------ banco local ---
def test_local_db_persists_between_restarts(tmp_path, monkeypatch):
    asyncio.run(_local_db_roundtrip(tmp_path, monkeypatch))


async def _local_db_roundtrip(tmp_path, monkeypatch):
    import db as dbmod
    f = tmp_path / "local.json"
    monkeypatch.setattr(dbmod, "_LOCAL_FILE", f)
    monkeypatch.setattr(dbmod, "_PERSIST", True)
    monkeypatch.setattr(dbmod, "_last_hash", None)
    if not dbmod.LOCAL_MODE:
        pytest.skip("só no modo memory://")
    await dbmod.db.settings.delete_many({})
    await dbmod.db.settings.insert_one({"key": "x", "value": {"when": datetime(2026, 1, 2, 3, 4)}})
    assert dbmod.flush_local_db() is True
    assert dbmod.flush_local_db() is False  # nada mudou → não regrava
    await dbmod.db.settings.delete_many({})
    assert await dbmod.db.settings.count_documents({}) == 0
    assert dbmod.load_local_db() >= 1
    doc = await dbmod.db.settings.find_one({"key": "x"})
    assert doc["value"]["when"] == datetime(2026, 1, 2, 3, 4)
    # arquivo corrompido não derruba o app
    f.write_text("{quebrado", encoding="utf-8")
    assert dbmod.load_local_db() == 0
    assert (tmp_path / "local.corrompido.json").exists()
    await dbmod.db.settings.delete_many({})
