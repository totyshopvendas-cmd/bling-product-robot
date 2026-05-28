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

## Arquitetura
- **Backend**: FastAPI + MongoDB (motor) + httpx + Playwright + emergentintegrations
- **Frontend**: React + Shadcn UI + Tailwind (tema Swiss/High-Contrast, Klein Blue #002FA7)
- **LLM**: Claude Haiku 4.5 (Emergent LLM key) — fallback opcional para limpeza
- **Automação JohnDrop**: Playwright/Chromium headless

## Implementado (28/05/2026)
- [x] Engine de limpeza de título (regex determinístico)
- [x] Engine LLM fallback (Claude Haiku via Emergent key)
- [x] Importação CSV de preços (99.901 linhas)
- [x] Lookup de preço por custo
- [x] Bling OAuth v3 (authorize, callback, refresh, disconnect)
- [x] Endpoints Bling (produtos, categorias)
- [x] Storage de credenciais JohnDrop em MongoDB
- [x] Robô Playwright (login, navegação, cadastro) — login confirmado funcionando
- [x] Modo MOCKED automático quando Playwright/Chromium indisponível
- [x] Sistema de logs em tempo real
- [x] Dashboard com métricas
- [x] UI completa em pt-BR (6 páginas)
- [x] 24/24 testes de backend passando

## Bloqueio em produção
- ⚠️ Conta JohnDrop do usuário está com **mensalidade atrasada** — modal bloqueia acesso ao catálogo. Robô faz login com sucesso mas vê apenas a tela de pagamento.

## Backlog (P1)
- Após pagamento JohnDrop: tunar seletores Playwright contra DOM real do catálogo
- Conectar conta Bling pela 1ª vez (botão Conectar Bling em Configurações)
- Adicionar criptografia Fernet das credenciais JohnDrop em DB
- Migrar @app.on_event para FastAPI lifespan

## Backlog (P2)
- Importação não-destrutiva do CSV (temp collection + swap)
- Webhook/cron para sync periódico Bling↔JohnDrop
- Multi-tenant (atualmente single-account)
