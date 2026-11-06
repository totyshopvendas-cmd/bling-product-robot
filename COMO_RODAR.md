# Como fazer o TotyShop funcionar (sem Emergent)

O painel roda **no seu computador** (ou num servidor seu). Ele precisa de internet normal para falar
com o Bling e com o JohnDrop, por isso as prévias online (Emergent, Arena) servem só para ver a tela.

## Windows — passo a passo

1. Instale [Python 3.11+](https://www.python.org/downloads/) (marque **Add python.exe to PATH**) e [Node.js 18+](https://nodejs.org/).
2. Baixe este repositório (GitHub → **Code → Download ZIP**, ou `git clone`) e descompacte, por exemplo em `C:\TotyShop`.
3. Dê dois cliques em **`iniciar_totyshop.bat`**.
   - Na primeira vez ele abre o Bloco de Notas com o arquivo `backend\.env`. Preencha:
     - `BLING_CLIENT_ID` e `BLING_CLIENT_SECRET` (Bling → Central de Extensões → seu aplicativo)
     - `APP_BASE_URL=http://127.0.0.1:8000`
     - `MONGO_URL=memory://local` (se você não tem MongoDB instalado — os dados ficam salvos em `backend\data\local_db.json`)
     - `APP_SECRET` = qualquer frase longa
     - `APP_PASSWORD` = deixe **vazio** no seu PC
   - Salve, feche o Bloco de Notas e aperte uma tecla na janela preta.
   - Ele instala tudo sozinho (demora uns minutos só na primeira vez) e abre o Chrome em `http://127.0.0.1:8000`.
4. **Não feche a janela preta** enquanto estiver usando — pode minimizar.

### Conectar o Bling

1. No app do Bling, em **Link de redirecionamento**, cole `http://127.0.0.1:8000/api/bling/callback` e salve.
2. No painel → **Configurações** → **Conectar Bling** → autorize.

Se o Bling recusar `http://127.0.0.1` (alguns apps exigem HTTPS), use um túnel (`ngrok http 8000`)
ou um servidor com domínio HTTPS, e coloque esse endereço no Bling e em `APP_BASE_URL`.

### IA local sem cobrança por chamada (Ollama)

O Ollama roda em um contêiner privado do Docker e não precisa de chave de API. Para gerar títulos, descrições, bullets e categorias sem enviar esses textos a um provedor pago:

1. Com o TotyShop instalado, abra o PowerShell na pasta do projeto.
2. Inicie o serviço local de IA:
   ```powershell
   docker compose --profile local-ai up -d
   docker compose exec ollama ollama pull qwen2.5:3b
   ```
   O download inicial do modelo pode demorar e usa espaço em disco.
3. No painel, abra **Configurações → Inteligência Artificial**. Em Ollama local, use `http://ollama:11434` e `qwen2.5:3b`; salve e teste a conexão.
4. Quando Ollama estiver configurado, o app prioriza esse modelo para texto, evitando chamadas pagas por engano. O computador pessoal precisa ficar ligado para o sistema trabalhar.

O modelo roda no seu computador, então a velocidade e qualidade dependem da memória e do processador. Ollama cobre texto; geração de imagens e voz ainda requerem provedores compatíveis e podem ter custo. O contêiner de IA não publica uma porta na rede: o endereço fica restrito à comunicação interna do Docker.

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
