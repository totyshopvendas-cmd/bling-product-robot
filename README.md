# TotyShop Automação

Painel independente para cadastro JohnDrop → Bling ERP (API v3). **Não depende da Emergent.**

> A prévia da Arena **não consegue** falar com o Bling nem baixar o Chromium (rede SSL bloqueada). Para o projeto funcionar de verdade, rode no seu PC — veja **[COMO_RODAR.md](COMO_RODAR.md)**. Windows: `iniciar_totyshop.bat`.

## O que foi corrigido na conexão Bling

O botão **Conectar Bling** usava URLs OAuth antigas (`https://bling.com.br/oauth/authorize` e `/oauth/token`). A API v3 exige:

| Etapa | URL oficial |
| --- | --- |
| Autorizar | `https://www.bling.com.br/Api/v3/oauth/authorize` |
| Trocar código / refresh | `https://www.bling.com.br/Api/v3/oauth/token` |
| API | `https://api.bling.com.br/Api/v3` |

Também passamos a enviar o header `enable-jwt: 1` (tokens opacos estão descontinuados) e o callback OAuth usa o endereço **desta** aplicação, não o domínio `*.preview.emergentagent.com`.

## Subir localmente

Pré-requisitos: Python 3.11+, Node 18+, MongoDB 6/7.

```bash
# 1. Variáveis
cp backend/.env.example backend/.env
# Edite BLING_CLIENT_ID, BLING_CLIENT_SECRET, APP_SECRET e APP_BASE_URL

# 2. Mongo
mongod --dbpath ~/.totyshop/mongo-data --port 27017 --bind_ip 127.0.0.1

# 3. Backend
cd backend
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn server:app --host 0.0.0.0 --port 8000

# 4. Frontend (outro terminal)
cd frontend
npm install
npm start
```

Windows: use `iniciar_totyshop.bat`.

Com o frontend em `http://localhost:3000`, as chamadas `/api` são enviadas ao backend na porta 8000.

### Um único processo (produção)

```bash
cd frontend && npm install && npm run build
cd ../backend && uvicorn server:app --host 0.0.0.0 --port 8000
```

O FastAPI serve o painel e a API no mesmo endereço. O callback OAuth fica em:

```
{APP_BASE_URL}/api/bling/callback
```

## Conectar o Bling (obrigatório uma vez)

1. Abra **Configurações** no painel.
2. Salve o **Client ID** e o **Client Secret** do aplicativo (Bling → Central de Extensões → Área do Integrador).
3. Copie o **Link de redirecionamento** exibido na tela.
4. Cole esse valor no campo correspondente do aplicativo Bling e salve.
5. Clique em **Conectar Bling** e autorize.

Sem o link de redirecionamento idêntico ao cadastrado no Bling, o OAuth falha.

## Docker

```bash
cp backend/.env.example .env
# preencha as variáveis, inclusive APP_BASE_URL público HTTPS
docker compose up --build
```

A aplicação fica em `http://localhost:8000`.

## LLM (opcional)

A limpeza de título por regex funciona sem chave. Para o fallback de IA, defina `OPENAI_API_KEY` no `.env`. A chave `EMERGENT_LLM_KEY` continua sendo aceita se você ainda a tiver.
