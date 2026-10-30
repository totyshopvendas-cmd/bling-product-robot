# TotyShop Automação

Painel próprio para automatizar a loja **JohnDrop → Bling ERP (API v3)**: cadastro de produtos pelo robô,
enriquecimento com IA (título, descrição, marca, categoria, cores), sincronização de estoque,
mapeamento de categorias por marketplace, anúncios para redes sociais e exportação de planilhas.

**100% independente da Emergent.** Roda no seu PC (Windows), num servidor/VPS ou em Docker.

> Para conectar no Bling e no JohnDrop, o painel precisa rodar num lugar com acesso normal à internet
> (seu PC ou servidor). Passo a passo para leigos: **[COMO_RODAR.md](COMO_RODAR.md)**.

---

## O que mudou em relação à versão da Emergent

| Antes (Emergent) | Agora |
| --- | --- |
| IA pela "Universal Key" da Emergent (`emergentintegrations`) | **Sua própria chave**: Anthropic, OpenAI, Gemini ou OpenRouter. Basta uma, com troca automática entre provedores. Configure em **Configurações → Inteligência Artificial** (com botão *Testar*) |
| Painel aberto para quem tivesse o link | **Senha opcional** (`APP_PASSWORD`), obrigatória se você publicar na internet |
| Dados presos no MongoDB da Emergent | **Backup / restauração** em um clique (Configurações → Backup) |
| Sem MongoDB → dados sumiam ao reiniciar | **Banco local persistente** em `backend/data/local_db.json` (modo `memory://local`) |
| Sync de estoque só manual | **Sync automático diário** no horário que você escolher (horário de Brasília) |
| Callback OAuth em `*.preview.emergentagent.com` | Callback no **seu** endereço: `{APP_BASE_URL}/api/bling/callback` |
| — | Exportação de planilha completa dos "Meus Produtos" do JohnDrop (`/exportar-jonhdrop`) |
| — | Docker com ffmpeg (vídeos), volumes persistentes e fuso de São Paulo |
| — | CI pronto para o GitHub (testes + build a cada push) — copie `docs/ci.yml.exemplo` para `.github/workflows/ci.yml` |

---

## Rodar no Windows (o jeito mais fácil)

1. Instale [Python 3.11+](https://www.python.org/downloads/) (marque **Add python.exe to PATH**) e [Node.js 18+](https://nodejs.org/).
2. Baixe o repositório e dê dois cliques em **`iniciar_totyshop.bat`**.
3. Na primeira vez ele cria o `backend\.env`, instala tudo e compila o painel. Depois abre `http://127.0.0.1:8000`.

Detalhes em [COMO_RODAR.md](COMO_RODAR.md).

## Rodar manualmente (Linux/Mac/servidor)

Pré-requisitos: Python 3.11+, Node 18+ e (opcional) MongoDB 6/7.

```bash
cp backend/.env.example backend/.env      # preencha BLING_CLIENT_ID/SECRET, APP_SECRET, APP_BASE_URL

# painel (uma vez, ou após atualizar)
cd frontend && npm install --legacy-peer-deps && npm run build && cd ..

# servidor (painel + API no mesmo endereço)
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium     # navegador do robô JohnDrop
uvicorn server:app --host 0.0.0.0 --port 8000
```

Sem MongoDB, use `MONGO_URL=memory://local` — os dados ficam salvos em `backend/data/local_db.json`.

Desenvolvimento do painel com recarga automática: `cd frontend && npm start` (porta 3000; `/api` vai para a 8000).

## Docker / VPS

```bash
cp backend/.env.example .env
# preencha BLING_*, APP_SECRET, APP_BASE_URL (HTTPS público) e APP_PASSWORD
docker compose up -d --build
```

Sobe MongoDB 7 + o painel em `http://localhost:8000`. Ficam em volumes (não se perdem ao atualizar):
banco (`totyshop_mongo`), chaves/segredos (`totyshop_secrets`), exportações (`totyshop_exports`) e a pasta `data/`.

Para atualizar: `git pull && docker compose up -d --build`.

## Conectar o Bling (uma vez)

1. **Configurações** → salve **Client ID** e **Client Secret** (Bling → Central de Extensões → Área do Integrador).
2. Copie o **Link de redirecionamento** mostrado na tela (`{APP_BASE_URL}/api/bling/callback`) e cole no app do Bling.
3. Clique em **Conectar Bling** e autorize.

URLs da API v3 usadas: autorizar `https://www.bling.com.br/Api/v3/oauth/authorize`, token
`https://www.bling.com.br/Api/v3/oauth/token`, API `https://api.bling.com.br/Api/v3` (com `enable-jwt: 1`).

## Inteligência Artificial

Cole **uma** chave em **Configurações → Inteligência Artificial** (ou no `.env`):

| Provedor | Usado para | Onde pegar |
| --- | --- | --- |
| Anthropic | textos (padrão Claude Haiku 4.5) | console.anthropic.com |
| OpenAI | textos, imagens (`gpt-image-1`) e narração (TTS) | platform.openai.com |
| Gemini | textos e imagens ("Nano Banana") — tem cota grátis | aistudio.google.com |
| OpenRouter | uma chave para vários modelos | openrouter.ai |

Ordem automática para texto: Anthropic → OpenAI → Gemini → OpenRouter. Para imagem: Gemini → OpenAI → OpenRouter.
Se um modelo for descontinuado, o sistema tenta o modelo padrão do provedor sozinho.
Forçar provedor/modelo: `AI_TEXT_PROVIDER`, `AI_TEXT_MODEL`, `AI_IMAGE_PROVIDER`, `AI_IMAGE_MODEL`.

A limpeza de títulos por regras funciona mesmo sem chave de IA.

## Segurança

- No PC, deixe `APP_PASSWORD` vazio. **Num servidor na internet, defina uma senha forte** e um `APP_SECRET` longo.
- As chaves ficam em `backend/.secrets/` e o banco local em `backend/data/` — ambos fora do Git.
- O arquivo de backup inclui os tokens do Bling: guarde-o em local seguro.

## Testes

```bash
cd backend && pytest tests            # testes unitários (não precisam de internet)
RUN_LIVE_TESTS=1 REACT_APP_BACKEND_URL=http://127.0.0.1:8000 pytest tests   # integração, com servidor rodando e Bling conectado
```
