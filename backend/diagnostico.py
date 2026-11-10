"""Diagnóstico de inicialização do TotyShop no PC do usuário.

Roda sem depender do servidor subir. Grava tudo em diagnostico.log para que
o resultado possa ser enviado ao suporte mesmo que a janela do console
congele (o famoso "QuickEdit" do Windows, que pausa a janela quando se
clica/seleciona texto dentro dela).

Uso:
    backend\\.venv\\Scripts\\python.exe -u backend\\diagnostico.py
ou simplesmente dê dois cliques em DIAGNOSTICO.bat
"""
from __future__ import annotations

import io
import os
import platform
import socket
import sys
import time
import traceback
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BACKEND_DIR.parent
LOG_FILE = PROJECT_ROOT / "diagnostico.log"

_buf = io.StringIO()


def say(msg: str = "") -> None:
    print(msg, flush=True)
    _buf.write(msg + "\n")
    try:
        LOG_FILE.write_text(_buf.getvalue(), encoding="utf-8")
    except Exception:
        pass


def step(n: str, title: str) -> None:
    say("")
    say("=" * 60)
    say(f"[{n}] {title}")
    say("=" * 60)


def mask(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return "(vazio)"
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]} ({len(value)} caracteres)"


def main() -> int:
    say("TotyShop - diagnostico de inicializacao")
    say(f"Data/hora: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    say(f"Log salvo em: {LOG_FILE}")

    # ------------------------------------------------------------------ 1
    step(1, "Python")
    say(f"versao .......: {sys.version.split()[0]}")
    say(f"executavel ...: {sys.executable}")
    say(f"pasta atual ..: {os.getcwd()}")
    say(f"pasta backend : {BACKEND_DIR}")
    in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    say(f"ambiente ..... : {'VENV (correto)' if in_venv else 'PYTHON DO SISTEMA (a .bat usa a .venv!)'}")
    say(f"sistema ......: {platform.platform()}")

    # ------------------------------------------------------------------ 2
    step(2, "Bibliotecas instaladas")
    faltando = []
    for mod, pip_name in [
        ("fastapi", "fastapi"),
        ("uvicorn", "uvicorn"),
        ("motor", "motor"),
        ("pymongo", "pymongo"),
        ("mongomock_motor", "mongomock-motor"),
        ("dotenv", "python-dotenv"),
        ("playwright", "playwright"),
        ("pandas", "pandas"),
        ("openpyxl", "openpyxl"),
        ("PIL", "Pillow"),
        ("requests", "requests"),
        ("openai", "openai"),
        ("httpx", "httpx"),
    ]:
        t0 = time.time()
        try:
            __import__(mod)
            say(f"  OK    {pip_name:<16} ({time.time() - t0:.2f}s)")
        except Exception as e:
            faltando.append(pip_name)
            say(f"  FALTA {pip_name:<16} -> {type(e).__name__}: {e}")

    # ------------------------------------------------------------------ 3
    step(3, "Arquivos de configuracao (.env)")
    for p in (PROJECT_ROOT / ".env", BACKEND_DIR / ".env"):
        if p.exists():
            linhas = p.read_text(encoding="utf-8", errors="replace").splitlines()
            chaves = [l.split("=", 1)[0].strip() for l in linhas if "=" in l and not l.strip().startswith("#")]
            say(f"  ENCONTRADO {p}  ({len(linhas)} linhas)")
            say(f"            chaves: {', '.join(chaves) if chaves else '(nenhuma)'}")
        else:
            say(f"  ausente    {p}")

    # valores efetivos (lendo os dois .env sem sobrescrever o ambiente)
    try:
        from dotenv import dotenv_values

        merged: dict[str, str] = {}
        for p in (BACKEND_DIR / ".env", PROJECT_ROOT / ".env"):
            if p.exists():
                merged.update({k: v for k, v in dotenv_values(p).items() if v})
        say("")
        for k in ("MONGO_URL", "DB_NAME", "APP_BASE_URL", "APP_PASSWORD",
                  "BLING_CLIENT_ID", "BLING_CLIENT_SECRET",
                  "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY"):
            v = os.environ.get(k) or merged.get(k) or ""
            if k.endswith(("_KEY", "_SECRET", "_ID")):
                say(f"  {k:<20} = {mask(v)}")
            else:
                say(f"  {k:<20} = {v or '(vazio)'}")
    except Exception as e:
        say(f"  (nao foi possivel ler o .env: {e})")

    # ------------------------------------------------------------------ 4
    step(4, "Porta 8000")
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(2)
    livre = s.connect_ex(("127.0.0.1", 8000)) != 0
    s.close()
    say(f"  porta 8000: {'LIVRE (ninguem escutando)' if livre else 'JA EM USO (outro programa/servidor)'}")

    # ------------------------------------------------------------------ 5
    step(5, "Importando o servidor (modulo por modulo)")
    sys.path.insert(0, str(BACKEND_DIR))
    os.chdir(BACKEND_DIR)
    modulos = [
        "db", "models", "title_cleaner", "pricing_service", "bling_service",
        "robot_service", "johndrop_bot", "dropper_bot", "shopee_bot",
        "llm_cleaner", "bling_enrichment", "bulk_enrichment",
        "social_service", "social_ad_service", "social_scheduler",
        "pinterest_service", "social_onboarding", "youtube_service",
        "enrichment_tracker", "enrich_worker", "image_proxy", "diag_service",
        "stock_sync", "stock_sync_bot", "johndrop_export", "review_service",
        "admin_service", "category_mapping", "category_mapping_bot",
    ]
    lento: list[tuple[str, float]] = []
    falhou: list[str] = []
    for m in modulos:
        t0 = time.time()
        try:
            __import__(m)
            dt = time.time() - t0
            marca = "  " if dt < 2 else "!!"
            say(f"  {marca} {m:<22} {dt:6.2f}s")
            if dt >= 2:
                lento.append((m, dt))
        except Exception as e:
            dt = time.time() - t0
            falhou.append(m)
            say(f"  XX {m:<22} {dt:6.2f}s  -> {type(e).__name__}: {e}")
            say("     " + traceback.format_exc().replace("\n", "\n     ").strip())

    say("")
    if falhou:
        say(f"  MODULOS QUE FALHARAM: {', '.join(falhou)}")
    if lento:
        say(f"  MODULOS LENTOS (>2s): {', '.join(f'{m} ({d:.1f}s)' for m, d in lento)}")
    if not falhou:
        say("  Todos os modulos importaram.")

    # ------------------------------------------------------------------ 6
    step(6, "Importando server:app completo")
    t0 = time.time()
    try:
        import server  # noqa: F401
        say(f"  server importado em {time.time() - t0:.2f}s")
        say("  App FastAPI criado com sucesso.")
    except Exception as e:
        say(f"  ERRO ao importar server: {type(e).__name__}: {e}")
        say(traceback.format_exc())
        say("")
        say(">>> ESTE E O MOTIVO DO PAINEL NAO ABRIR. Envie este log.")
        return 1

    # ------------------------------------------------------------------ 7
    step(7, "Subindo o servidor de teste e chamando a API")
    try:
        import threading

        import uvicorn

        config = uvicorn.Config(server.app, host="127.0.0.1", port=8001,
                                log_level="warning")
        srv = uvicorn.Server(config)
        th = threading.Thread(target=srv.run, daemon=True)
        th.start()
        ok = False
        for _ in range(40):
            time.sleep(0.5)
            try:
                import urllib.request

                with urllib.request.urlopen("http://127.0.0.1:8001/api/", timeout=2) as r:
                    say(f"  API respondeu {r.status}: {r.read(120).decode(errors='replace')}")
                ok = True
                break
            except Exception:
                continue
        if not ok:
            say("  A API nao respondeu na porta 8001 em 20s.")
            say("  (se a etapa 6 passou, tente rodar a .bat como Administrador)")
        srv.should_exit = True
    except Exception as e:
        say(f"  falha no teste de subida: {type(e).__name__}: {e}")

    say("")
    say("=" * 60)
    say("FIM DO DIAGNOSTICO - envie o arquivo diagnostico.log")
    say("=" * 60)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception:
        say("ERRO INESPERADO NO DIAGNOSTICO:")
        say(traceback.format_exc())
        sys.exit(1)
    finally:
        try:
            LOG_FILE.write_text(_buf.getvalue(), encoding="utf-8")
        except Exception:
            pass
