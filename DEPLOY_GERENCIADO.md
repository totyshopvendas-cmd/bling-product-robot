# TotyShop online 24h — modo simplificado (Railway)

Este é o caminho recomendado quando você quer abrir o TotyShop no celular e em qualquer computador **sem administrar VPS, domínio, DNS, Linux ou Docker**.

A hospedagem gerenciada fornece uma URL HTTPS pronta, semelhante a:

```text
https://totyshop-production-xxxx.up.railway.app
```

Essa URL já serve para o painel e para o retorno do Bling. Um domínio próprio é opcional e pode ser configurado depois.

> A Railway é um serviço pago de hospedagem gerenciada. Confirme preço, limites de memória e disponibilidade diretamente na tela da Railway antes de contratar. O TotyShop deve ficar em plano que **não adormeça** e deve usar um **Volume** persistente.

---

## O que será mantido no servidor

Um único Volume persistente guarda:

- dados do painel, filas, logs e tabela de preços;
- tokens e credenciais salvos pelo painel;
- arquivos de exportação JohnDrop;
- cópia local da tabela de preços.

Os robôs JohnDrop e TrimPro ficam no serviço online, portanto o PC de casa não precisa ficar ligado. Mantenha **apenas uma réplica** do serviço: duas cópias executariam robôs e agendamentos duplicados.

## Antes de começar

Você só precisa de:

1. uma conta Railway criada por você;
2. acesso ao repositório GitHub do TotyShop;
3. uma senha exclusiva para o painel;
4. os dados do aplicativo Bling, que podem ser salvos posteriormente dentro do painel.

**Não envie senha da Railway, senha do GitHub, cartão, APP_SECRET, APP_PASSWORD, Client Secret ou token do Bling pelo chat.**

---

## Publicação pela tela, sem comandos

### 1. Criar o projeto

1. Entre na Railway e clique em criar um projeto novo.
2. Escolha a opção de implantar a partir de um repositório GitHub.
3. Autorize somente o repositório `totyshopvendas-cmd/bling-product-robot`.
4. Selecione a branch `main`.

**Resultado esperado:** aparece um serviço novo do TotyShop e a Railway inicia a construção pelo `Dockerfile` do projeto. A imagem é criada primeiro; depois que o painel estiver ativo, ele prepara o navegador Chromium dos robôs automaticamente em segundo plano. Isso pode levar alguns minutos na primeira vez.

### 2. Criar a URL HTTPS pronta

No serviço criado, abra **Settings / Networking** e clique para gerar um domínio público.

**Resultado esperado:** a Railway mostra uma URL `https://...up.railway.app`. Copie apenas essa URL para usar no passo seguinte.

### 3. Criar o disco persistente

No mesmo serviço, abra **Volumes** e adicione um Volume.

Preencha o ponto de montagem exatamente assim:

```text
/app/persist
```

Escolha um tamanho inicial que comporte exportações e backups. Não remova esse Volume depois que começar a usar o painel.

**Resultado esperado:** a Railway mostra o Volume anexado ao serviço.

### 4. Inserir as variáveis pela tela

Abra **Variables** no serviço. Adicione estas variáveis, uma por vez:

| Variável | Valor |
| --- | --- |
| `MONGO_URL` | `memory://local` |
| `LOCAL_DB_PERSIST` | `1` |
| `TOTYSHOP_PERSIST_DIR` | `/app/persist` |
| `TZ` | `America/Sao_Paulo` |
| `DB_NAME` | `bling_robot` |
| `APP_BASE_URL` | a URL HTTPS gerada no passo 2, sem barra no final |
| `APP_SECRET` | uma sequência longa, aleatória e exclusiva |
| `APP_PASSWORD` | sua senha forte e exclusiva do painel |

Para gerar `APP_SECRET`, use o botão de gerar valor da própria plataforma, se ele estiver disponível. Caso não exista, use um gerenciador de senhas para criar uma sequência aleatória longa. Não reutilize senha do e-mail, GitHub ou Bling.

**Resultado esperado:** após salvar as variáveis, a Railway faz um novo deploy. Espere o serviço aparecer como ativo/verde.

> Não adicione `REACT_APP_BACKEND_URL`: o painel e a API ficam na mesma URL e já se conectam automaticamente.

### 5. Conferir o painel

Abra a URL pública em uma janela anônima do navegador ou no celular.

**Resultado esperado:** aparece a tela com o campo **Senha do painel**. Entre com o valor de `APP_PASSWORD`.

No menu do TotyShop, confira se o indicador mostra **backend conectado**. Depois abra **Configurações** e faça primeiro um backup vazio: ele confirma que o painel consegue gravar no Volume.

---

## Migrar o que já existe no PC

A instalação local continua como backup.

1. No TotyShop do PC atual, abra **Configurações → Backup** e baixe o arquivo `.json`.
2. Abra o TotyShop online, entre com a senha e vá em **Configurações → Restaurar backup**.
3. Envie o mesmo arquivo.
4. Confira a tabela de preços, configurações e filas antes de ativar qualquer robô em modo real.

**Não execute os robôs em modo real no PC local e no painel online ao mesmo tempo.** Depois da migração, use o painel online como instalação principal.

## Conectar o Bling

Na Central de Extensões do Bling, configure o link de redirecionamento com a URL pública da Railway:

```text
https://SUA-URL-DA-RAILWAY.up.railway.app/api/bling/callback
```

No painel online, abra **Configurações**, salve Client ID/Client Secret do Bling se necessário e clique para conectar. O retorno precisa acontecer na mesma URL pública do painel.

## Primeiro uso seguro

1. Configure/valide credenciais do JohnDrop e TrimPro pelo painel online.
2. Confira a tabela de preços.
3. Execute primeiro cada robô em **Modo Teste**.
4. Confirme no Bling e no fornecedor se o resultado do teste está correto.
5. Só então use o modo real.

## Backup e atualização

- Baixe periodicamente **Configurações → Backup** para seu computador ou armazenamento seguro.
- O Volume mantém os dados entre reinícios e deploys, mas não substitui um backup externo.
- Quando houver atualização do TotyShop, a Railway pode implantar automaticamente a branch `main`. Faça backup antes de uma atualização importante.

## Diagnóstico simples

| Situação | Confira primeiro |
| --- | --- |
| URL não abre | serviço ativo/verde e domínio público gerado na Railway |
| Painel abre, mas não aceita senha | valor de `APP_PASSWORD` e se houve novo deploy após salvar a variável |
| Dados desapareceram após reiniciar | Volume anexado em `/app/persist` e `TOTYSHOP_PERSIST_DIR=/app/persist` |
| Bling rejeita o retorno | `APP_BASE_URL` e callback devem ser idênticos, incluindo `https://` |
| Robô não inicia logo após o primeiro deploy | espere alguns minutos para o Chromium terminar de ser preparado; depois confira Configurações, credenciais, tabela de preços e primeiro teste no painel |

## Limite desta opção

A hospedagem gerenciada torna o uso simples, mas as contas e pagamentos continuam sob seu controle. Este projeto não cria nem recebe acesso à sua conta Railway, GitHub, Bling ou cartão. Nunca compartilhe esses dados em conversas.
