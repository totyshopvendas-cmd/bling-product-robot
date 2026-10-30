"""Configuração do pytest.

Os testes abaixo são de INTEGRAÇÃO: chamam um servidor TotyShop rodando, com
Bling/JohnDrop conectados de verdade (foram escritos para a prévia antiga da
Emergent). Ficam desligados por padrão. Para rodar contra o seu servidor:

    RUN_LIVE_TESTS=1 REACT_APP_BACKEND_URL=http://127.0.0.1:8000 pytest tests
"""
import os

os.environ.setdefault("MONGO_URL", "memory://")
os.environ.setdefault("LOCAL_DB_PERSIST", "0")

LIVE_TESTS = [
    "backend_test.py",
    "test_category_mapping_known_lojas.py",
    "test_category_mapping_lojas_gaps.py",
    "test_category_mapping_marketplaces_api.py",
    "test_category_mapping_ui_backend.py",
    "test_recent_skus_and_worker.py",
    "test_social_ad.py",
]

collect_ignore = [] if os.environ.get("RUN_LIVE_TESTS") == "1" else LIVE_TESTS
