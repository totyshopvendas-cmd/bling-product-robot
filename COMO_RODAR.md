# Como fazer o TotyShop funcionar de verdade

## Por que a prévia da Arena não conecta

Acabei de testar o servidor desta prévia:

- `https://www.bling.com.br` e `https://api.bling.com.br` → conexão SSL cortada
- Download do Chromium (Playwright) → a mesma rede bloqueia

Por isso o **Conectar Bling** e o **Instalar Chromium** nunca fecham o ciclo **dentro deste quadro**. Não é o Client ID, não é o aplicativo 334699, e não é o código OAuth v3 (os testes unitários passam).

O painel na Arena serve para ver a interface. **A conexão real com o Bling e o robô JohnDrop só completam no seu computador** (ou num servidor seu), onde a internet chega no Bling e no Chromium.

## O que já está pronto no código

- OAuth Bling API v3 (`/Api/v3/oauth/authorize` + `/Api/v3/oauth/token` + `enable-jwt: 1`)
- Callback `{endereço-do-painel}/api/bling/callback`
- Client ID do seu app (`97fe6685…`) pode ser colado de novo em Configurações
- Painel + API no mesmo processo na porta **8000** (sem Emergent)

## Caminho que funciona (Windows)

1. Instale [Python 3.11+](https://www.python.org/downloads/) (marque **Add python.exe to PATH**) e [Node 18+](https://nodejs.org/).
2. Baixe este repositório (GitHub → Code → Download ZIP, ou `git clone`).
3. Copie `backend/.env.example` para `backend/.env`.
4. Preencha:
   - `BLING_CLIENT_ID` e `BLING_CLIENT_SECRET` (Bling → Informações do app)
   - `APP_BASE_URL=http://127.0.0.1:8000`
   - `MONGO_URL=memory://local` (se você não tiver MongoDB instalado)
   - `APP_SECRET` = qualquer frase longa
5. Dê dois cliques em `iniciar_totyshop.bat`.
6. O Chrome abre `http://127.0.0.1:8000` **em janela normal** (não no quadro da Arena).
7. No Bling, **Dados básicos**, cole:
   `http://127.0.0.1:8000/api/bling/callback`
   e salve.
8. No painel → **Configurações** → **Conectar Bling**. O login abre nesta mesma janela. Autorize.
9. O Chromium instala sozinho na primeira execução (precisa de internet). Depois o modo REAL do robô JohnDrop funciona.

Se o Bling recusar `http://127.0.0.1` (alguns apps exigem HTTPS), use Docker atrás de um domínio HTTPS, ou um túnel (`ngrok http 8000`) e coloque esse HTTPS no **Dados básicos** e em `APP_BASE_URL`.

## Docker (alternativa)

```bash
cp backend/.env.example .env
# preencha BLING_CLIENT_ID, BLING_CLIENT_SECRET, APP_SECRET
# APP_BASE_URL=http://127.0.0.1:8000
docker compose up --build
```

Abra `http://127.0.0.1:8000` e siga os passos 7–8 acima.

## Como saber que deu certo

- Configurações → selo **Conectado** no Bling, botão **Testar conexão** responde OK
- Robô JohnDrop → “Chromium do servidor pronto”
- Aí sim o projeto está no ar, independente da Emergent e da Arena
