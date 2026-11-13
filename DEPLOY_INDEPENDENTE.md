# Publicação do TotyShop — painel online 24h

Este guia mantém a instalação atual do Windows intacta como cópia de segurança e publica **uma segunda instalação** em um VPS Linux. Essa instalação fica ligada 24 horas, centraliza dados e robôs e é acessada por HTTPS no celular, PC de casa ou PC da organização.

> **O que a publicação não faz:** não copie a pasta `.env`, `backend/.secrets`, `backend/data` nem backups para GitHub. Os segredos ficam exclusivamente no servidor e nos volumes Docker.

## Arquitetura segura

```text
Navegador (celular / PCs) → HTTPS :443 → Caddy → TotyShop :8000 → MongoDB
                                          ├─ Chromium / JohnDrop / TrimPro
                                          ├─ worker de enriquecimento
                                          └─ agendadores
```

O Caddy obtém e renova automaticamente o certificado HTTPS. O MongoDB não possui porta pública e o FastAPI `:8000` tampouco: somente o proxy HTTPS o alcança.

## Antes de começar

Você precisa ter:

1. Um VPS Linux com Docker Compose (recomendado: Ubuntu LTS, **4 GB RAM / 2 vCPU / 40 GB SSD** ou maior).
2. Um domínio ou subdomínio. Exemplo usado abaixo: `automacao.seudominio.com`.
3. O DNS desse subdomínio apontando para o IP público do VPS com um registro **A**.
4. As portas **80** e **443** liberadas no firewall/provedor. Elas são necessárias para o HTTPS automático.
5. A URL final cadastrada no Bling: `https://automacao.seudominio.com/api/bling/callback`.

A URL precisa ser definida antes de autorizar o Bling, porque o callback OAuth deve corresponder exatamente ao endereço público.

## 1. Preparar o servidor

No terminal do VPS, instale Docker Engine e o plugin Compose conforme a documentação do provedor/da Docker. Depois, execute cada comando separadamente:

```bash
git clone https://github.com/totyshopvendas-cmd/bling-product-robot.git totyshop
```

**Saída esperada:** a pasta `totyshop` é criada.

```bash
cd totyshop
```

**Saída esperada:** o terminal passa a terminar em `/totyshop`.

```bash
cp backend/.env.example .env
```

**Saída esperada:** nenhuma mensagem de erro.

Gere o segredo de sessão, sem expô-lo no chat nem no GitHub:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

**Saída esperada:** uma linha longa aleatória. Guarde-a temporariamente para o próximo passo.

Edite o arquivo `.env` com `nano .env` (ou o editor de sua preferência). Preencha ao menos estas linhas; troque os exemplos pelos valores reais:

```dotenv
APP_BASE_URL=https://automacao.seudominio.com
APP_SECRET=COLE_AQUI_O_SEGREDO_ALEATORIO_GERADO
APP_PASSWORD=CRIE_UMA_SENHA_LONGA_E_UNICA_DO_PAINEL
TOTYSHOP_DOMAIN=automacao.seudominio.com
DB_NAME=bling_robot
```

Também adicione no mesmo `.env` as credenciais do aplicativo Bling, se você já as possui:

```dotenv
BLING_CLIENT_ID=seu-client-id
BLING_CLIENT_SECRET=seu-client-secret
```

**Nunca envie o conteúdo do `.env` por mensagem, e-mail ou GitHub.** Use uma senha exclusiva com pelo menos 16 caracteres; um gerenciador de senhas é recomendado.

## 2. Publicar com HTTPS

Ainda dentro da pasta do projeto, execute:

```bash
docker compose -f docker-compose.yml -f docker-compose.online.yml config
```

**Saída esperada:** o Compose imprime a configuração unificada sem erros. Verifique se em `caddy` aparece o domínio desejado e se a porta do `app`, quando exibida, está vinculada apenas a `127.0.0.1:8000` (nunca `0.0.0.0:8000`).

```bash
docker compose -f docker-compose.yml -f docker-compose.online.yml up -d --build
```

**Saída esperada:** os serviços `mongo`, `app` e `caddy` iniciam ou são recriados.

```bash
docker compose -f docker-compose.yml -f docker-compose.online.yml ps
```

**Saída esperada:** os três serviços aparecem como `running`. O Caddy exibe as portas públicas `80` e `443`; a porta opcional do `app` deve aparecer somente como `127.0.0.1:8000`.

```bash
docker compose -f docker-compose.yml -f docker-compose.online.yml logs --tail=80 caddy
```

**Saída esperada:** mensagens indicando obtenção/renovação bem-sucedida do certificado, sem erro de DNS. Se ocorrer erro de certificado, confirme primeiro o registro DNS e as portas 80/443.

Abra `https://automacao.seudominio.com` em uma janela normal do navegador. A tela deve pedir a `APP_PASSWORD`. Teste também pelo celular usando dados móveis; assim você confirma que não é apenas a rede interna.

## 3. Migrar o conteúdo da instalação local (opcional, recomendado)

A instalação Windows continua preservada. Para levar as configurações, filas e tabela de preços ao servidor, use **Configurações → Backup** no TotyShop local para baixar o `.json`. Em seguida, no painel online, entre com a senha e use **Configurações → Restaurar backup** para enviar o mesmo arquivo.

Depois da restauração, confira no painel online:

1. tabela de preços carregada;
2. credenciais/configurações dos robôs, sem revelar as senhas em tela;
3. status do Bling;
4. uma execução de teste do JohnDrop e do TrimPro.

Como os tokens OAuth podem expirar ou serem vinculados à URL anterior, faça uma nova autorização do Bling no painel online se o status não estiver conectado.

## 4. Configurar o callback do Bling

No cadastro do aplicativo no Bling, use exatamente:

```text
https://automacao.seudominio.com/api/bling/callback
```

No painel online, acesse **Configurações**, salve o Client ID/Client Secret quando necessário e clique para conectar o Bling. Complete a autorização na mesma janela do navegador. O endereço pode ter parâmetros após o callback; isso é normal.

## Operação diária

- Acesse sempre a URL HTTPS pelo PC ou celular.
- Os robôs JohnDrop e TrimPro passam a executar no VPS; o PC de casa não precisa ficar ligado.
- Não rode os mesmos robôs em modo real simultaneamente no PC local e no painel online. Use o painel online como instalação principal após a migração.
- Mantenha a instalação local parada ou apenas como backup, para evitar duas filas concorrentes.

## Backup no servidor

Pelo painel, baixe periodicamente **Configurações → Backup**. Para uma cópia completa do banco no VPS, execute:

```bash
cd ~/totyshop
```

**Saída esperada:** você volta à pasta do projeto (ajuste o caminho se clonou em outro local).

```bash
docker compose -f docker-compose.yml -f docker-compose.online.yml exec -T mongo mongodump --archive --gzip > backup-$(date +%Y%m%d-%H%M%S).archive.gz
```

**Saída esperada:** um arquivo `backup-AAAAMMDD-HHMMSS.archive.gz` é criado. Baixe-o para um local seguro fora do VPS.

Para restaurar, pare as operações/robôs primeiro e execute, substituindo o nome do arquivo:

```bash
cat backup-AAAAMMDD-HHMMSS.archive.gz | docker compose -f docker-compose.yml -f docker-compose.online.yml exec -T mongo mongorestore --drop --archive --gzip
```

**Saída esperada:** `mongorestore` relata as coleções restauradas. O `--drop` substitui os dados atuais: só o use quando tiver certeza.

## Atualizar o TotyShop

Faça um backup antes. Então, no VPS:

```bash
cd ~/totyshop
```

**Saída esperada:** você está na pasta clonada.

```bash
git pull origin main
```

**Saída esperada:** aparece a atualização recebida, ou `Already up to date.`.

```bash
docker compose -f docker-compose.yml -f docker-compose.online.yml up -d --build
```

**Saída esperada:** o app é reconstruído e volta a `running`; dados e segredos ficam preservados nos volumes Docker.

## Diagnóstico rápido

```bash
docker compose -f docker-compose.yml -f docker-compose.online.yml ps
```

**Saída esperada:** `mongo`, `app`, `caddy` em execução.

```bash
docker compose -f docker-compose.yml -f docker-compose.online.yml logs --tail=120 app
```

**Saída esperada:** logs do FastAPI, carregamento da tabela e do Chromium, sem rastreamento de erro repetido.

```bash
curl -fsS https://automacao.seudominio.com/healthz
```

**Saída esperada:** JSON com `"status":"ok"`.

> Não poste logs que revelem tokens, senhas, Client Secret ou conteúdo do `.env`.

## Por que não publicar a porta 8000 diretamente?

A configuração mantém o app, quando necessário, ligado apenas em `127.0.0.1:8000`, e só expõe HTTPS público pelo Caddy. Isso protege o transporte da senha e mantém cookies de sessão seguros. Para um teste local no próprio PC, use somente `docker-compose.yml`; para o VPS online, use sempre os dois arquivos `-f` deste guia.
