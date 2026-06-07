# TotyShop Automation — PRD

## Problema Original
Automatizar cadastro de produtos do JohnDrop no Bling ERP + enriquecimento conforme manual TotyShop.

## Fluxo de Trabalho (alinhado ao manual)

### FASE 1: Robô JohnDrop
- Login automático, navega para "Produtos não cadastrados"
- Para cada produto: limpa SKU/título, busca preço na CSV, cadastra
- Bling recebe via sync passivo (TRAVA DE CONCORRÊNCIA: nada é editado via API até sync completar)

### FASE 2: Enriquecimento (após produto chegar ao Bling)
- **Descrição curta** SEO em parágrafos com `<b>negrito</b>`, sem marcas/EAN
- **Descrição complementar**: 8 bullets ≤150 chars, com negrito
- **Marca**: "Generico" (masculino — manual TotyShop)
- **Tipo Produção**: "T" (Terceiros)
- **Condição**: 1 (Novo)
- **GTIN** + **GTIN tributário**: zerados
- **Categoria**: SÓ existentes — manual proíbe criação de novas categorias pela IA
- **Fornecedor**: vinculado via POST `/produtos/fornecedores` com JONH VARIEDADES + ID JohnDrop + Custo
- **Variações** (cores/tamanhos): PATCH `/produtos/{id}` com `formato=V`, `actionEstoque=Z`, `variacoes=[]` → Bling cria automaticamente. Estoque distribuído igualmente (Regra Balanceada).
- **Imagens**: chegam pelo sync nativo JohnDrop→Bling (não precisamos extrair)

## Arquitetura
- **Backend**: FastAPI + MongoDB + Playwright + emergentintegrations
- **Frontend**: React + Shadcn + Tailwind (tema TotyShop laranja)
- **LLM**: Claude Haiku 4.5 via Emergent LLM Key
- **Bling API v3** OAuth 2.0

## Endpoints principais
- `POST /api/robot/start` — inicia bot Playwright
- `POST /api/bling/enrich` — enriquecimento manual de 1 SKU
- `POST /api/bling/enrich-bulk` — fila em lote
- `POST /api/bling/variations` — cria variações manualmente
- `GET /api/bling/products-with-status` — lista produtos com flag enriched
- `GET /api/system/chromium-status` — diagnóstico Chromium

## Funcionalidades validadas (31/05/2026)
- [x] Robô Playwright completo (login → catálogo → cadastro)
- [x] Auto-instalação Chromium dinâmica (qualquer versão)
- [x] Banner visual de status Chromium
- [x] Detecção e skip de SKU duplicado no JohnDrop
- [x] Descrição SEO + 8 bullets via LLM
- [x] Marca "Generico" + Tipo Produção "T" via PATCH
- [x] Categoria SÓ existentes (manual proibe criar novas)
- [x] Fornecedor JONH VARIEDADES vinculado via POST /produtos/fornecedores
- [x] Variações via PATCH com formato=V/actionEstoque=Z (fluxo único que Bling aceita)
- [x] Distribuição balanceada de estoque entre variações
- [x] Enriquecimento em lote com seleção + "todos não enriquecidos"
- [x] Job background com progresso ao vivo

## Validado em produção (KA-966 e Copo-260ComTampa)
- Marca: ✅ Generico
- Tipo Produção: ✅ T
- Fornecedor: ✅ JONH VARIEDADES com código JohnDrop 119689 e custo R$ 9,99
- Variações: ✅ 3 criadas (Cor:Rosa/Azul/Verde), estoque 10 cada (30 total ÷ 3)

## Atualizações 06/02/2026
### Correções
- **Estoque em produtos novos**: novo helper `_read_parent_stock_with_retry` em `bling_variations.py` faz polling do `/estoques/saldos` com até 6 tentativas × 10s (≈60s), evitando que o sync atrasado do JohnDrop→Bling resulte em variações com estoque zero.
- **Imagens em variações**: removido filtro indevido que descartava URLs S3 presigned do JohnDrop (`AWSAccessKeyId` / `X-Amz-Signature`). Imagens agora são copiadas para cada variação filha via `PATCH /produtos/{id}` com `midia.imagens.imagensURL`.
- Cleanup de código morto em `bling_variations.py` (função `fix_existing_variations` reescrita).

### Módulo Criar Anúncio
- Novo serviço `social_ad_service.py` (registrado em `/api/social/ad/*`)
- `GET /ad/products`: lista produtos enriquecidos elegíveis (filtra variações filhas)
- `POST /ad/generate`: gera imagem (Gemini Nano Banana 1080×1080) + copy (Claude Haiku 4.5) para anúncio. Retorna `draft_id`, URL absoluta da imagem, headline + caption.
- `GET /ad/asset/{id}.png`: serve a imagem gerada (armazenada em base64 no Mongo `social_ad_assets`)
- `POST /ad/publish`: publica simultaneamente no Instagram (2-step media + media_publish) e Facebook Page (`/photos`)
- `GET /ad/drafts`: histórico de anúncios gerados
- Frontend: nova página `/criar-anuncio` com fluxo seleção→briefing→preview→publicar

## Atualizações 07/02/2026
### Robô separado do enriquecimento (P0)
- **Robô JohnDrop agora SÓ cadastra produtos no Bling no formato bruto** (sem disparar enriquecimento automático)
- O usuário deve rodar manualmente "Enriquecer em Lote" → filtro "Não enriquecidos" depois que o JohnDrop completar o sync de estoque + imagens (~2-3min após cadastro)
- Isso resolve definitivamente os problemas recorrentes de:
  - Estoque zerado em variações (sync JohnDrop chegava após a conversão formato=V)
  - Imagens faltando (sync JohnDrop chegava após o PATCH)
- Linhas alteradas: `johndrop_bot.py` ~912 (removido `asyncio.create_task(_safe_enrich_bling)`)

### Seleção de Página Meta (P0)
- Novo endpoint `GET /api/social/meta/pages` lista todas as páginas que o token tem acesso, com Instagram linkado
- Novo endpoint `POST /api/social/meta/select-page` para escolher página + auto-detectar IG vinculado
- Novo botão "Escolher Página" (azul) na UI Redes Sociais
- **Page ID corrigido** de `37252617084329081` (TotyShop antigo) para `6937722930491369` (TotyShop.com, a página correta com Instagram vinculado)

### Melhorias de UX nos erros
- Pinterest "consumer type not supported" agora exibe explicação completa com passos para "Apply for Production" no dev console
- Status Meta na UI mostra mensagem amarela explicativa quando Instagram Business não está vinculado, com link para business.facebook.com
- Botão "Escolher Página" mostra estado claro: token expirado, IG por página, página atualmente selecionada
## Atualizações 06/02/2026 (continuação)
### Otimização /ad/products (P2-a)
- Nova coleção MongoDB `bling_enriched_cache` populada automaticamente após enriquecimento
- `/ad/products` agora retorna em ~13ms (antes ~30s para 30 produtos — **2300× mais rápido**)
- Endpoint POST `/ad/products/backfill` (fire-and-forget) para popular cache de produtos já enriquecidos
- GET `/ad/products/backfill-status` para monitorar progresso

### Agendador de Anúncios (P2-b)
- Worker assíncrono em `social_scheduler.py` roda a cada 60s
- Horários de pico padrão (Brasil): 12h, 18h, 21h
- Endpoints:
  - `POST /ad/schedule` — agenda 1 anúncio para próximo pico (ou ISO específico)
  - `POST /ad/schedule/bulk` — agenda vários distribuídos pelos picos
  - `GET /ad/schedule` — lista agendamentos (filtros: pending/published/failed/cancelled)
  - `DELETE /ad/schedule/{id}` — cancela
  - `GET /ad/scheduler/status` — estado do worker
- Frontend: nova página `/agenda` com tabela, filtros por status, cancelamento
- Botão "Agendar para próximo pico" no Criar Anúncio
- Retry automático até 3 tentativas em caso de falha

### Pinterest (P2-c)
- Serviço `pinterest_service.py` com endpoints:
  - `POST /social/pinterest/credentials` — salva access token criptografado
  - `POST /social/pinterest/test` — valida token + retorna username
  - `GET /social/pinterest/boards` — lista boards do usuário
  - `POST /social/pinterest/pin` — cria pin (image_url + title + description + board_id)
- UI integrada na página Redes Sociais (seção Pinterest abaixo de Meta)
- Checkbox Pinterest no Criar Anúncio (multi-channel select)
- `/ad/publish` agora aceita `publish_pinterest=true` + `pinterest_board_id`

## YouTube Shorts (próxima sprint — P2-d)
- Mais complexo: requer OAuth 2.0 Google + refresh tokens + resumable upload + geração de vídeo (9:16)
- Stack proposta: imagem 1080×1920 (Nano Banana) + áudio TTS Claude/OpenAI + ffmpeg merge
- Quota crítica: `videos.insert` = 100/dia por projeto Google Cloud

## Backlog (P1/P2)
- **P1**: Usuário renovar Long-Lived Page Access Token Meta usando botão "Tornar Token Vitalício"
- **P1**: Usuário configurar `instagram_business_id` (linkar Instagram Business à Página)
- **P1**: Opção "Limpar e re-enriquecer" (inativar variações erradas no Bling)
- **P2**: YouTube Shorts (OAuth + resumable upload + geração de vídeo)
- **P2**: Botão "Materializar imagens no Bling" (UI guide para o toggle manual)
- **P2**: Conta JohnDrop com mensalidade atrasada (depende do user)
- **P2**: Migrar `@app.on_event` → FastAPI lifespan
- **P2**: Limpeza de warnings de hooks React em Robot.js / Settings.js
