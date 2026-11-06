# Como fazer o TotyShop funcionar (sem Emergent)

O painel roda **no seu computador** (ou num servidor seu). Ele precisa de internet normal para falar
com o Bling e com o JohnDrop, por isso as prévias online (Emergent, Arena) servem só para ver a tela.

## Windows — passo a passo

1. Instale [Python 3.11+](https://www.python.org/downloads/) e [Node.js 18+](https://nodejs.org/) se o iniciador não conseguir fazê-lo automaticamente.
2. Baixe a versão atualizada do projeto e descompacte em uma pasta local, por exemplo `C:\TotyShop`.
3. Dê dois cliques em **`iniciar_totyshop.bat`**. Na primeira vez, ele prepara as dependências e o Chromium; pode levar alguns minutos e precisa de internet.
4. O iniciador usa uma configuração local e uma interface focada em JohnDrop, Dropper, Shopee e enriquecimento. Em **Configurações → Bling**, salve o Client ID e o Client Secret do aplicativo Bling; não os envie pelo chat.
5. **Não feche a janela preta** enquanto estiver usando — pode minimizá-la. O painel abre em `http://127.0.0.1:8000`.

### Conectar o Bling

1. No app do Bling, em **Link de redirecionamento**, cole `http://127.0.0.1:8000/api/bling/callback` e salve.
2. No painel → **Configurações** → **Conectar Bling** → autorize.

Se o Bling recusar `http://127.0.0.1` (alguns apps exigem HTTPS), use um túnel (`ngrok http 8000`)
ou um servidor com domínio HTTPS, e coloque esse endereço no Bling e em `APP_BASE_URL`.

### IA local sem cobrança por chamada (Ollama)

No Windows, dá para usar IA local **sem Docker** (recomendado se a virtualização estiver desativada):

1. Instale Ollama em https://ollama.com/download.
2. Abra o PowerShell e rode `ollama pull qwen2.5:1.5b`.
3. Inicie o TotyShop pelo arquivo `iniciar_totyshop.bat`.
4. No painel, em **Configurações → Inteligência Artificial**, use o endereço `http://127.0.0.1:11434` e o modelo `qwen2.5:1.5b`; salve e teste.

O modelo escolhido é menor para tentar funcionar melhor em computadores com memória limitada. Ainda pode ficar lento: este PC precisa estar ligado e o Ollama aberto para usar a IA. Ollama cobre texto; geração de imagens e voz ainda requerem provedores compatíveis e podem ter custo.

Se o projeto estiver rodando via Docker em vez do iniciador Windows, o Ollama também pode rodar em contêiner: `docker compose --profile local-ai up -d`, depois `docker compose exec ollama ollama pull qwen2.5:1.5b`. Nesse caso, no painel use `http://ollama:11434`.

### IA por provedor externo (opcional)

Se preferir uma IA hospedada, crie uma chave em **um** destes sites (a disponibilidade e os limites gratuitos podem mudar):
   - Google Gemini → https://aistudio.google.com/apikey
   - OpenAI → https://platform.openai.com/api-keys
   - Anthropic (Claude) → https://console.anthropic.com
   - OpenRouter → https://openrouter.ai/keys
2. Painel → **Configurações → Inteligência Artificial** → cole a chave → **Salvar** → **Testar**.

Para gerar **imagens** de anúncio use Gemini ou OpenAI. Para **narração** de vídeo, OpenAI.

### Configurar o Dropper

1. Abra **Configurações** no painel.
2. No cartão **Dropper**, informe o e-mail e a senha usados em `dropper.dropping.com.br` e salve.
3. Abra **Robô Dropper** e mantenha o **Modo Teste** na primeira execução para validar os seletores e a leitura dos produtos.
4. Confirme que a tabela de preços está carregada. No modo real, o robô limpa título e SKU, calcula o preço de venda, cadastra somente no Dropper e registra o SKU para aguardar a integração nativa com o Bling.

O robô Dropper não envia imagens, estoque ou cadastros diretamente para a API do Bling. Depois que a integração nativa entregar o produto, o worker de enriquecimento usa o contato fornecedor definido em `DROPPER_SUPPLIER_NAME` (padrão `DROPPER`).

### Sync de estoque automático

Painel → **Sync de Estoque** → marque **Rodar sozinho todo dia às** e escolha o horário (horário de Brasília).
O computador precisa estar ligado com o painel aberto. Se o PC estiver desligado no horário,
o sync roda assim que você abrir o painel naquele dia.

### Backup

Painel → **Configurações → Backup** → **Baixar backup**. Guarde o arquivo (ele contém os tokens do Bling).
Para levar tudo para outro PC ou servidor: instale lá e use **Restaurar de um arquivo**.

### Atualizar para uma versão nova

Baixe o repositório de novo (ou `git pull`) por cima da pasta e rode `iniciar_totyshop.bat`.
Suas configurações (`backend\.env`, `backend\.secrets`, `backend\data`) são preservadas.

## Servidor / VPS (painel 24 h no ar)

```bash
git clone https://github.com/totyshopvendas-cmd/bling-product-robot.git && cd bling-product-robot
cp backend/.env.example .env
# edite .env: BLING_*, APP_BASE_URL=https://seu-dominio, APP_SECRET longo e APP_PASSWORD forte
docker compose up -d --build
```

Coloque um proxy HTTPS na frente (Caddy, Nginx, Cloudflare Tunnel) apontando para a porta 8000.
Com `APP_PASSWORD` definido, o painel pede senha antes de abrir.

## Como saber que deu certo

- Configurações → selo **Conectado** no Bling e **Testar conexão** responde OK
- Configurações → Inteligência Artificial → **Testar** responde OK
- Robô JohnDrop → "Chromium do servidor pronto"

## Problemas comuns

| Sintoma | Solução |
| --- | --- |
| Chrome mostra "conexão recusada" | A janela preta foi fechada — rode `iniciar_totyshop.bat` de novo |
| Bling diz "redirect_uri inválido" | O link no app do Bling tem que ser **idêntico** a `{APP_BASE_URL}/api/bling/callback` |
| "Nenhuma chave de IA configurada" | Cole uma chave em Configurações → Inteligência Artificial |
| Vídeo de anúncio não gera | Instale o ffmpeg (https://ffmpeg.org) — no Docker ele já vem |
