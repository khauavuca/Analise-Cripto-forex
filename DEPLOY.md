# Coleta 24/7 na Oracle Cloud (Always Free)

Guia para deixar a coleta rodando sem depender da sua máquina. O serviço só lê
mercado público e grava observações — **nenhuma ordem é enviada, e nenhuma chave
de API é necessária.**

## Por que Oracle e por que São Paulo

O tier **Always Free** da Oracle é gratuito por tempo indeterminado, não é trial
de 12 meses. Ele dá VM de verdade com disco persistente, que é o que o SQLite
precisa.

A região **São Paulo (GRU)** importa por dois motivos. A Binance responde 451
para faixas de IP de vários provedores de nuvem nos EUA, e a latência a partir do
Brasil é menor. Se ainda assim a corretora bloquear, trocar é uma linha no
`.env` — foi para isso que o projeto usa CCXT.

## 1. Criar a conta

| O quê | Link |
|---|---|
| Criar conta gratuita | <https://signup.cloud.oracle.com/> |
| Página do free tier | <https://www.oracle.com/cloud/free/> |
| Console (depois de criar) | <https://cloud.oracle.com/> |

Na inscrição, escolha **Brazil East (São Paulo)** como *home region*. Ela não
pode ser trocada depois — a conta fica presa à região escolhida.

Pede cartão de crédito para verificar identidade. Não há cobrança enquanto você
ficar dentro dos limites Always Free.

## 2. Criar a máquina

No console: **Compute → Instances → Create Instance**, ou direto em
<https://cloud.oracle.com/compute/instances/create>.

| Campo | Valor |
|---|---|
| Imagem | Canonical Ubuntu 24.04 |
| Shape | `VM.Standard.A1.Flex` — 1 OCPU, 6 GB |
| Chave SSH | envie a sua chave pública |

O limite Always Free do Ampere A1 é hoje **2 OCPUs e 12 GB no total** — a Oracle
cortou pela metade em 15/06/2026, sem aviso público. 1 OCPU e 6 GB ficam dentro
da metade da cota, o que deixa margem caso você queira uma segunda máquina.

Se o shape ARM aparecer sem capacidade — acontece bastante em São Paulo — use
`VM.Standard.E2.1.Micro`, também Always Free. É bem mais fraco, mas a coleta é
leve: o gargalo é rede, não CPU.

Anote o IP público ao final.

### Atenção: máquina ociosa pode ser recuperada

Em conta **Always Free**, a Oracle recupera instâncias consideradas ociosas —
critério aproximado de CPU, rede e memória abaixo de 20% por 7 dias seguidos.

Isso importa aqui: **o coletor é leve de propósito.** Ele passa quase todo o
tempo dormindo entre consultas, então cai exatamente no perfil de "ociosa" e
pode ser desligada no meio de semanas de coleta.

A saída padrão é converter a conta para **Pay As You Go** no console. Contas
pagas não sofrem recuperação por ociosidade, e os recursos Always Free continuam
sem custo — você só passa a poder gastar caso ultrapasse a cota. Vale conferir a
fatura no primeiro mês para confirmar que está zerada.

Se preferir não converter, o efeito prático é que a coleta pode parar sozinha
depois de alguns dias, e você precisa recriar a máquina. Os backups em
`dados/backups` é que evitam perder o histórico nesse caso.

## 3. Preparar o servidor

```bash
ssh ubuntu@SEU_IP
```

```bash
sudo apt update && sudo apt install -y docker.io docker-compose-v2 sqlite3 git
sudo usermod -aG docker ubuntu && newgrp docker
```

A Oracle vem com o firewall local fechado por padrão. Como o serviço **não abre
porta nenhuma** — ele só faz conexões de saída — não é preciso liberar nada.

## 4. Subir o projeto

```bash
git clone https://github.com/Khauazin/Analise-Cripto-forex.git ~/analise-cripto
cd ~/analise-cripto && git checkout motor-backtest-ccxt
```

```bash
cp .env.example .env && nano .env
```

Ajuste `PARES`, `TIMEFRAMES` e `EXCHANGE` se quiser. Não há chave a preencher.

Crie as pastas de dados **antes** de subir, com o dono certo:

```bash
mkdir -p dados logs && sudo chown -R 10001:10001 dados logs
```

Isso não é detalhe. O container roda como usuário 10001, não root. Se as pastas
não existirem, o Docker as cria pertencendo ao root, o container não consegue
escrever e entra em laço de reinício com um erro de permissão que não diz
claramente o que aconteceu.

```bash
docker compose up -d --build
```

A primeira construção demora alguns minutos — ela compila as dependências para a
arquitetura da VM (ARM, se você usou o shape `A1.Flex`).

## 5. Conferir que está coletando

```bash
docker compose logs -f --tail 50
```

Você deve ver a lista de setups, e depois linhas de pulso a cada poucos minutos.
Sinais aparecem quando algum setup dispara — pode levar horas, é o esperado.

Para contar o que já entrou no banco:

```bash
sqlite3 ~/analise-cripto/dados/dados_mercado.db "SELECT estrategia, COUNT(*) velas, SUM(direcao!=0) sinais FROM observacoes GROUP BY estrategia;"
```

## 6. Backup automático

O disco da VM é persistente, mas VM não é backup: instância removida leva o
disco junto. Uma cópia diária resolve.

```bash
chmod +x ~/analise-cripto/scripts/backup.sh
( crontab -l 2>/dev/null; echo "0 3 * * * $HOME/analise-cripto/scripts/backup.sh >> $HOME/backup.log 2>&1" ) | crontab -
```

O script usa `sqlite3 .backup` em vez de `cp` — copiar um SQLite enquanto ele
está sendo escrito gera um arquivo corrompido que só acusa erro na hora de ler,
semanas depois.

## 7. Trazer os dados para analisar

A análise continua rodando na sua máquina; o servidor só coleta.

```bash
scp ubuntu@SEU_IP:~/analise-cripto/dados/dados_mercado.db ./dados_coletados.db
```

```bash
python cli.py calibrar --banco dados_coletados.db
```

## Operação do dia a dia

| Ação | Comando |
|---|---|
| Ver logs | `docker compose logs -f --tail 100` |
| Parar | `docker compose stop` |
| Retomar | `docker compose start` |
| Atualizar o código | `git pull && docker compose up -d --build` |
| Espaço em disco | `df -h && du -sh ~/analise-cripto/dados` |

`restart: unless-stopped` faz o container voltar sozinho depois de reboot da VM
ou de queda do processo. Como cada vela é gravada assim que fecha, uma parada no
meio não perde o que já foi coletado — só as velas do período em que ficou fora.

## Problemas comuns

**`451 Unavailable For Legal Reasons`** — a corretora bloqueou o IP. Troque
`EXCHANGE` no `.env` para `bybit`, `okx` ou `kraken` e reinicie. Os pares seguem
os mesmos.

**Container reiniciando em laço** — `docker compose logs --tail 50`. Quase sempre
é `.env` ausente ou par que não existe na corretora escolhida.

**Disco cheio** — o log já é limitado a 50 MB no compose. Se apertar, são os
backups: reduza `MANTER` no `backup.sh`.

**Sem sinal nenhum depois de horas** — normal. Os setups são seletivos: em 4h,
alguns emitem poucas entradas por mês. A tabela `observacoes` cresce mesmo assim,
porque toda vela fechada é gravada, com sinal ou sem.
