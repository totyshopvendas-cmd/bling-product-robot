# TotyShop Automation — PRD

## Problema Original
Sistema para empresa TotyShop automatizando cadastro de produtos no fornecedor JohnDrop (https://app.jonhdrop.com.br) e sincronização com ERP Bling via API v3 (OAuth 2.0).

### Regras de Limpeza de Título
1. Remover marcas (XLS, Kapbom, Inova, Altomex, Eletromex, etc)
2. Remover EANs (sequências 8-14 dígitos)
3. Código do produto no FINAL (ex: KA-6070, JZ-USBD)
4. Sem caracteres especiais — apenas hífen permitido
5. Máximo 60 caracteres

### Regra de Preço
- Custo do JohnDrop → lookup na tabela CSV (col 1 = Custo Catálogo) → col 3 (Preço de Venda inteiro, sem pontuação, ex: 21,99 → 5050) → colar em "Preço de Venda" no JohnDrop

### Regras de Enriquecimento Bling
- Descrição curta SEO em parágrafos com `<b>...</b>` e hífen (sem marcas/EAN)
- 8 bullets técnicos (≤150 chars cada)
- Marca = "Generica", Condição = 1 (Novo), GTIN limpo
- Fornecedor = JONH VARIEDADES (com Título + ID JohnDrop + Custo)
- Categoria mapeada via LLM + keyword fallback; **cria nova categoria automaticamente se não existir**

## Arquitetura
- **Backend**: FastAPI + MongoDB (motor) + httpx + Playwright + emergentintegrations
- **Frontend**: React + Shadcn UI + Tailwind (tema TotyShop, laranja #EE7B22)
- **LLM**: Claude Haiku 4.5 (Emergent LLM key)
- **Automação JohnDrop**: Playwright/Chromium headless

## Implementado
- [x] Engine de limpeza de título (regex determinístico + LLM fallback)
- [x] Importação CSV de preços + lookup por custo
- [x] Bling OAuth v3 (authorize, callback, refresh, disconnect)
- [x] Storage de credenciais JohnDrop em MongoDB
- [x] Robô Playwright completo (login, navegação, limpeza SKU, preço, submit)
- [x] Auto-instalação Chromium no boot
- [x] Enriquecimento Bling pós-cadastro (descrição, 8 bullets, categoria)
- [x] Categoria: cria automaticamente no Bling se não existir
- [x] Sistema de logs em tempo real + Dashboard
- [x] **(31/05/2026) Detecção de SKU duplicado no JohnDrop** — bot loga "warning" e pula em vez de travar 45s
- [x] **(31/05/2026) Página "Enriquecer em Lote"** — lista produtos Bling com status enriched/pendente, seleção em massa, varredura "todos não enriquecidos", job em background com progresso ao vivo

## Endpoints novos
- `GET /api/bling/products-with-status?pagina=&limite=&filtro=&busca=` — produtos com flag `enriched`
- `POST /api/bling/enrich-bulk` — inicia job em lote (lista de IDs OU `enrich_all_not_enriched: true`)
- `GET /api/bling/bulk-job` — estado do job em execução
- `POST /api/bling/bulk-job/stop` — interrompe job

## Backlog (P1)
- Conta JohnDrop com mensalidade atrasada bloqueia catálogo (depende do usuário pagar)
- Adicionar criptografia Fernet das credenciais JohnDrop em DB
- Migrar @app.on_event para FastAPI lifespan

## Backlog (P2)
- Importação não-destrutiva do CSV (temp collection + swap)
- Webhook/cron para sync periódico Bling↔JohnDrop
- Multi-tenant (atualmente single-account)
- Limpeza de hooks React (lint warnings)
