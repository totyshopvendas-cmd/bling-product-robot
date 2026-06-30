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

## Atualizações 13/02/2026 (P2 Backlog)

### YouTube Shorts (P2-d) ⭐ — IMPLEMENTADO
- Novo módulo `youtube_service.py` com pipeline completo:
  - **OAuth 2.0 Google**: endpoints `/youtube/credentials`, `/youtube/oauth/start` (redirect consent), `/youtube/oauth/callback` (captura refresh_token + channel info)
  - **TTS via Universal Key**: `OpenAITextToSpeech` (model tts-1, voz `nova`, formato MP3)
  - **Geração de vídeo 9:16 com ffmpeg**:
    - Imagem 1:1 do anúncio é padded para 1080x1920 com fundo borrado (boxblur=40)
    - Combina com áudio MP3 em MP4 (libx264, yuv420p, AAC 192k, 30fps)
  - **Upload resumable**: POST `/upload/youtube/v3/videos?uploadType=resumable` → PUT do binário → retorna video_id
  - **Refresh automático** de access_token via refresh_token salvo (válido permanentemente)
- Adicionado ao Setup Wizard com guia 3-passos (Google Cloud project + OAuth Client ID + Conectar)
- Adicionado checkbox no Criar Anúncio + resultado mostrado no preview
- Adicionado ao `social_onboarding`: check do status de credenciais + autorização
- ffmpeg instalado no container

### Códigos nas variações (SKU + sigla)
- `bling_variations.py`: cada variação criada agora recebe `codigo: <parent_sku>-<sigla>`
- Função `_variation_sigla()` mapeia 30+ cores/tamanhos comuns (Azul→AZ, Verde→VD, Branco→BR, Pequeno→P, etc.)
- Fallback para nomes desconhecidos: primeiros 2 caracteres alfanuméricos uppercase

### Correções enriquecimento (P0) ⭐
- **Bug crítico identificado**: o campo `descricao` no Bling está VAZIO após o JohnDrop sincronizar. Por isso o bulk re-enrichment perdia o texto bruto e nunca conseguia parsear as variações (cores/tamanhos).
- **Solução**: nova coleção MongoDB `product_raw` armazena o texto cru do JohnDrop indexado por SKU, persistido por `enrich_product_by_sku` no início de cada execução.
- Quando o bulk chama `_enrich_one` e o Bling retorna `descricao` vazio, o sistema consulta o `product_raw` e recupera o texto original — variações são corretamente parseadas e criadas.
- Removido o "skip já enriquecido" no bulk — agora o usuário pode forçar re-enrich para criar variações em produtos antigos.
- Novos endpoints para produtos pré-existentes (sem raw persistido):
  - `POST /api/bling/raw-description` salva manualmente o texto bruto de um SKU
  - `GET /api/bling/raw-description/{sku}` consulta se já existe
- "Limpar campos" no Enriquecimento Bling: após sucesso, o form é limpo automaticamente; também adicionado botão manual "Limpar campos".

### Setup Wizard de Redes Sociais (P0 — 07/02)
- Página `/setup-redes` com checklist visual + guia passo-a-passo
- Backend `GET /api/social/onboarding/status` agrega status de cada integração:
  - Meta: credenciais / token válido / página selecionada / IG vinculado
  - Pinterest: token funcional (detecta Sandbox) / board padrão
- UI: barra de progresso, próxima ação destacada, status colorido por item, guias expandíveis, banner "Pronto para publicar!" quando token+página OK
- Detecta token expirado e Pinterest Sandbox com instruções específicas para resolver

### 1. Republicação de drafts falhados
- Novo endpoint `POST /api/social/ad/republish/{draft_id}` que reusa imagem + caption gerados (sem custo de LLM/Nano Banana) e re-executa o flow de publicação Meta + Pinterest
- Botão "Republicar" aparece no histórico de drafts (Criar Anúncio) para itens com status `failed` ou `draft`
- Caso de uso: usuário renovou o token Meta — agora basta clicar Republicar nos drafts antigos sem regerar nada

### 2. Geração + agendamento em lote
- Novo endpoint `POST /api/social/ad/batch/generate` (fire-and-forget) gera anúncios para N produtos e agenda automaticamente nos picos 12h/18h/21h ao longo de N dias
- `GET /api/social/ad/batch/status` retorna progresso em tempo real (total / gerados / agendados / falhas)
- Limite de segurança: 30 produtos por lote (proteção contra estouro do orçamento da Universal Key)
- UI: botão "Modo Lote" no Criar Anúncio → checkboxes nos product cards → "Gerar e Agendar N" → barra de progresso

### 3. Dashboard de progresso do enriquecimento ⭐
- Novo módulo `enrichment_tracker.py` mantém em memória o estágio de cada SKU
- Estágios: queued → waiting_sync → enriching → done/failed
- Integrado em `bling_enrichment.enrich_product_by_sku` — track automático em cada transição
- Endpoint `GET /api/enrich/progress` + `DELETE /api/enrich/progress` (limpa histórico)
- Nova página `/progresso` com:
  - 4 cards de resumo (total / ativos / concluídos / falhas)
  - Tabela com SKU, produto, estágio (chip colorido), detalhe (estoque+imgs em real-time), timestamp
  - Auto-refresh a cada 3 segundos (toggle on/off)
  - Botão "Limpar histórico"

### YouTube Shorts (P2-d — não implementado)
- Adiado para próxima sprint. Complexidade: OAuth 2.0 Google + ffmpeg para geração de vídeo 9:16 + TTS de áudio + resumable upload
- Quota crítica: `videos.insert` = 100/dia por projeto Google Cloud

- Nova página `/setup-redes` com checklist visual + guia passo-a-passo
- Backend: `GET /api/social/onboarding/status` agrega o estado de cada integração:
  - Meta: credenciais salvas / token válido / página selecionada / Instagram vinculado
  - Pinterest: token funcional (detecta Sandbox) / board padrão
- UI mostra:
  - Barra de progresso geral (X/6 prontos)
  - Cartão "Próxima ação" destacando o próximo passo (com botão direto)
  - Status colorido de cada item (verde/amarelo/vermelho/cinza)
  - Guias expandíveis com instruções completas (Meta + Pinterest)
  - Banner "Pronto para publicar!" quando token + página estão OK
  - Links externos diretos: business.facebook.com (Instagram), developers.pinterest.com (Production)
- Detecta token expirado e exibe mensagem específica
- Detecta app Pinterest em Sandbox e orienta como aplicar para Produção


- Restaurado o trigger automático de enriquecimento após cadastro JohnDrop
- **Novo**: função `_wait_for_johndrop_sync(product_id, sku)` em `bling_enrichment.py` que aguarda o sync JohnDrop→Bling completar antes de prosseguir com a conversão formato=V
- Critério de "sync completo": estoque saldoVirtualTotal > 0 **OU** ao menos 1 imagem em midia.imagens — qualquer dos dois sinaliza que o JohnDrop terminou de empurrar o produto
- Polling: 12 tentativas × 15s = até **3 minutos** de espera, retornando imediatamente assim que detecta o sync
- Reutiliza imagens da Bling (em vez das URLs S3 presigned do JohnDrop) quando disponíveis — URLs Bling são mais estáveis
- **4 testes unitários** em `test_wait_sync.py` validam: stock pronto, imagens prontas, polling até chegar, timeout gracioso
- Resultado esperado: variações criadas com estoque correto distribuído + imagens em cada variação filha, automaticamente, sem intervenção manual

### Robô separado do enriquecimento — REVERTIDO
- O usuário decidiu manter o trigger automático, mas com a espera inteligente acima

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

## Atualizações 22/02/2026

### Correção: imagens nas variações + Backfill via CSV ⭐
- **Bug**: `_copy_images_to_children` em `bling_variations.py` tinha sido convertida em no-op por um agente anterior assumindo que "Bling silenciosamente ignora PATCH em variações". **Errado**. Restaurei o PATCH com `imagensURL` e validei: Bling aceita perfeitamente quando as URLs são as internas do próprio Bling (testado em KA-9103: 3/3 variações com 6 imagens cada após refetch do pai).
- Agora produtos NOVOS cadastrados com variações terão imagens replicadas em todos os children automaticamente.
- **Backfill por CSV**: script `/tmp/backfill_csv_imgs.py` que lê o CSV exportado do Bling, extrai o `Jonhdrop id` da coluna Observações, abre a página JonhDrop de cada produto, extrai as URLs das imagens originais e faz PATCH no Bling (com retry e limite de 4 imagens por chamada para evitar 504 do Cloudflare). Resultado em 39 produtos: 20 pais + 10 variações corrigidos, 17 já OK, 0 erros, 7m27s.

### Sincronização de Estoque JohnDrop → Bling (NOVO MÓDULO ISOLADO)
- Módulo `stock_sync.py` + scraper `stock_sync_bot.py` + página `/estoque-sync`.
- Endpoints: `POST /api/stock-sync/run`, `GET /api/stock-sync/status`.
- Fluxo: login JonhDrop → varre "Meus Produtos" (100/pág, paginado) + "Ver todos Alertas" → atualiza Bling. Para variações: respeita "esgotado"/quantidade específica na descrição; restante divide igualmente. Preço também atualizado via PATCH.
- Bugs corrigidos durante validação:
  1. SKU parseado errado ("GDR1015 Catálogo: GDR1015") — split por "Catálogo:" literal
  2. Page-size 100/pág não aplicado — JS evaluate force change event
  3. Sino → "Ver todos Alertas" não clicado — JS evaluate com `closest('a, button')`
- 8 testes unitários cobrindo distribuição (par, ímpar, esgotado, explícito, mix, zero, excedente, única).
- Sync de teste: 499 SKUs catalogados em 1m20s, 38/39 atualizações na fase inicial (97% sucesso).

### Correção: worker liberava enriquecimento sem imagens (revertido)
- Bug introduzido nesta sessão e revertido na mesma: o gate voltou a ser **APENAS imagens** (a "bagagem" pousou).
- Removido `FORCE_AFTER_ATTEMPTS`, `MAX_ATTEMPTS=80` (~2h paciência).
- 4 testes de regressão.

### Correção: parser de variações descartava cores compostas
- "Cinza com preto" (3 palavras) era filtrada → resultado colapsava para `[]` (single item rule).
- Limite subido para 3 palavras, mantendo filtros descritivos.
- 5 testes de regressão.

### Aba "Últimos 50 SKUs" no Enriquecimento em Lote
- Endpoint `GET /api/bling/recent-skus?limit=50` agrega de `enrich_pending` + `product_raw` + `bling_enriched_cache`. Latência <5s.
- UI `/bling-lote`: aba padrão "Últimos 50" com badges, banner de erro, checkbox desabilitado para itens sem product_id.
- Endpoint utilitário `DELETE /api/bling/raw-description/{sku}`.

### Diagnóstico de imagens faltantes (causa raiz)
- Storage do plano Bling tinha estourado — todos os produtos novos chegavam sem imagem.
- Hipótese do usuário confirmada via varredura de 100 produtos: 0% com imagem na página 1 vs 100% na página 4. Usuário liberou espaço — problema resolvido.

### Status final do projeto
- ✅ Robô JohnDrop cadastra produtos (cadastro + variações + códigos)
- ✅ Worker delayed aguarda imagens chegarem do sync nativo JohnDrop
- ✅ Enriquecimento completo: descrição + 8 bullets + marca "Generico" + condição "Novo" + produção "Terceiros" + fornecedor JONH VARIEDADES
- ✅ Variações com códigos `<sku>-<sigla>` (AZ, VD, PT) e estoque balanceado
- ✅ Imagens replicadas em todas as variações
- ✅ Sync Estoque diário JohnDrop→Bling
- ✅ Módulo Social: Meta (FB/IG), Pinterest, YouTube Shorts (setup wizard + scheduler)
- ✅ Onboarding/Setup wizard de redes sociais

### Backlog ativo

### Sincronização de Estoque JohnDrop → Bling ⭐ (NOVO MÓDULO ISOLADO)
- Novo módulo `stock_sync.py` + scraper `stock_sync_bot.py` totalmente isolados (NÃO tocam o fluxo de cadastro existente).
- Endpoints: `POST /api/stock-sync/run`, `GET /api/stock-sync/status`.
- Página `/estoque-sync` com botão manual + tabela de resultados (atualizados / sem Bling / erros).
- Fluxo:
  1. Scraper faz login na JohnDrop, varre **Meus Produtos** (paginado, escolhe 100/página) → SKU + estoque + preço.
  2. Abre **Alertas** via URL direta ou clicando no sino → "Ver todos Alertas". Capta alertas de "Preço atualizado" (extrai novo preço via regex `R$ X para R$ Y`).
  3. Merge: alertas têm precedência sobre catálogo quando há sobreposição.
  4. Para cada SKU:
     - Busca produto no Bling pelo código exato. Se não existir → ignora (per regra do usuário).
     - **Variações**: lê `product_raw.raw_description`, identifica nomes via `_parse_variations` e quantidades específicas via `_parse_variation_quantities` (suporta "esgotado", "Cor: 5", "(5 un)" etc.). Cores esgotadas recebem 0; com número específico recebem aquele valor; o restante divide o estoque remanescente.
     - **Simples**: POST /estoques (operacao=B Balanço).
     - **Preço**: PATCH /produtos/{id} se diferente do atual.
- Persistência em `stock_sync_runs` com run_id + reports detalhados.
- 8 testes unitários em `/app/backend/tests/test_stock_sync.py` validando distribuição (par/ímpar, esgotado, explícito, mix, zero, excedente, única).

### Bug fix: Worker liberava enriquecimento sem imagens
- Bug introduzido nesta sessão e revertido: o gate voltou a ser **APENAS imagens** (a "bagagem" pousou).
- Removido `FORCE_AFTER_ATTEMPTS`, `MAX_ATTEMPTS` aumentado para 80 (~2h paciência).
- 4 testes de regressão em `/app/backend/tests/test_worker_gate_and_parser.py`.

### Bug fix: Parser de variações descartava cores compostas
- "Cinza com preto" e "Vermelho com preto" (3 palavras) eram filtradas, fazendo o resultado colapsar para `[]`.
- Subido limite para 3 palavras, mantendo filtros de frases descritivas.
- 5 testes de regressão cobrindo cores simples, compostas, disclaimer, e filtros descritivos.

### Aba "Últimos 50 SKUs" no Enriquecimento em Lote
- Endpoint `GET /api/bling/recent-skus?limit=50` agrega de `enrich_pending` + `product_raw` + `bling_enriched_cache`. Latência <5s.
- UI `/bling-lote`: aba padrão "Últimos 50" com badges Enriquecido/Pendente/Aguardando Bling, banner de erro, checkbox desabilitado para itens sem product_id.
- Endpoint utilitário `DELETE /api/bling/raw-description/{sku}` para limpar dados de teste.

### Bling Storage detectado cheio (root cause de imagens não aparecerem)
- Investigação revelou que produtos pós-determinada data ficavam sem imagens no Bling.
- Causa: storage do plano Bling cheio. Usuário liberou espaço — problema resolvido.

### Backlog ativo
- **P1**: Usuário renovar Long-Lived Page Access Token Meta usando botão "Tornar Token Vitalício"
- **P1**: Usuário configurar `instagram_business_id` (linkar Instagram Business à Página)
- **P1**: Opção "Limpar e re-enriquecer" (inativar variações erradas no Bling)
- **P2**: Reconciler de background para preencher `product_id` em `enrich_pending` (substitui o fallback de 10 chamadas inline)
- **P2**: Botão "Materializar imagens no Bling" (UI guide para o toggle manual)
- **P2**: Conta JohnDrop com mensalidade atrasada (depende do user)
- **P2**: Migrar `@app.on_event` → FastAPI lifespan
- **P2**: Limpeza de warnings de hooks React em Robot.js / Settings.js
