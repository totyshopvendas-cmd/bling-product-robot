# Deploy independente do Emergent

O TotyShop Automação pode rodar como uma aplicação própria, sem depender de Emergent. A distribuição usa um único contêiner da aplicação e um MongoDB persistente via Docker Compose.

## Arquitetura

```text
Navegador → HTTPS/reverse proxy → TotyShop (FastAPI + React :8000) → MongoDB
                                      ├─ Playwright/Chromium
                                      ├─ worker de enriquecimento
                                      └─ scheduler de estoque e redes sociais
```

O frontend é compilado dentro do `Dockerfile` e servido pelo próprio FastAPI. Não é necessário manter um servidor Node separado em produção.

## Opção 1 — executar no computador próprio

Pré-requisitos: Docker Desktop ou Docker Engine com Compose.

```bash
git clone https://github.com/totyshopvendas-cmd/bling-product-robot.git
cp backend/.env.example .env
```

Edite `.env` e defina pelo menos:

```dotenv
APP_BASE_URL=http://127.0.0.1:8000
APP_SECRET=troque-por-um-segredo-longo-e-aleatorio
APP_PASSWORD=troque-por-uma-senha-do-painel
MONGO_URL=mongodb://mongo:27017
BLING_CLIENT_ID=seu-client-id
BLING_CLIENT_SECRET=seu-client-secret
```

Suba a aplicação:

```bash
docker compose up -d --build
```

Acesse `http://127.0.0.1:8000`. Para acompanhar:

```bash
docker compose ps
docker compose logs -f app
```

Os dados persistem nos volumes `totyshop_mongo`, `totyshop_secrets`, `totyshop_exports` e `totyshop_data`.

## Opção 2 — VPS com domínio próprio

No VPS Ubuntu, instale Docker, clone o repositório e use o mesmo `docker compose up -d --build`. Configure um reverse proxy HTTPS (Caddy, Nginx ou Traefik) apontando para `127.0.0.1:8000`.

Com domínio, altere:

```dotenv
APP_BASE_URL=https://automacao.seudominio.com
```

O callback do Bling deve ser exatamente:

```text
https://automacao.seudominio.com/api/bling/callback
```

Depois de atualizar o código:

```bash
git pull origin main
docker compose up -d --build
```

## Primeira configuração

1. Abra **Configurações** e defina as credenciais do painel.
2. Configure o Client ID e Client Secret do Bling.
3. Autorize o Bling usando o callback do seu domínio.
4. Carregue a tabela de preços.
5. Configure as credenciais do JohnDrop.
6. Execute primeiro os robôs em **Modo Teste**.
7. Só depois habilite o modo real.

## Backup e atualização

Backup do MongoDB:

```bash
docker compose exec -T mongo mongodump --archive --gzip > backup-$(date +%Y%m%d).archive.gz
```

Restaurar:

```bash
cat backup-AAAAMMDD.archive.gz | docker compose exec -T mongo mongorestore --archive --gzip
```

O `.env`, os volumes Docker e os tokens OAuth não devem ser enviados ao GitHub. O arquivo `.dockerignore` já exclui segredos, bancos locais, exportações e caches.

## Limites importantes

Esta distribuição é independente do Emergent, mas ainda depende das contas externas que você escolher usar: Bling, JohnDrop e provedores de IA. Para deixá-la pública e sempre ligada, é necessário um computador ou VPS ligado continuamente; o sandbox desta sessão não é um servidor permanente.
