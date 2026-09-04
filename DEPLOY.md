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

## 1. Criar a máquina

No console da Oracle Cloud: **Compute → Instances → Create Instance**.

| Campo | Valor |
|---|---|
| Região | Brazil East (São Paulo) |
| Imagem | Canonical Ubuntu 24.04 |
| Shape | `VM.Standard.A1.Flex` — 1 OCPU, 6 GB (Always Free) |
| Chave SSH | envie a sua chave pública |

Se o shape ARM (`A1.Flex`) aparecer sem capacidade — acontece com frequência —
use `VM.Standard.E2.1.Micro`, que também é Always Free. Ele é bem mais fraco,
mas a coleta é leve: o gargalo é rede, não CPU.

Anote o IP público ao final.

## 2. Preparar o servidor

```bash
ssh ubuntu@SEU_IP
```

```bash
sudo apt update && sudo apt install -y docker.io docker-compose-v2 sqlite3 git
sudo usermod -aG docker ubuntu && newgrp docker
```

A Oracle vem com o firewall local fechado por padrão. Como o serviço **não abre
porta nenhuma** — ele só faz conexões de saída — não é preciso liberar nada.

## 3. Subir o projeto

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

## 4. Conferir que está coletando

```bash
docker compose logs -f --tail 50
```

Você deve ver a lista de setups, e depois linhas de pulso a cada poucos minutos.
Sinais aparecem quando algum setup dispara — pode levar horas, é o esperado.

Para contar o que já entrou no banco:

```bash
sqlite3 ~/analise-cripto/dados/dados_mercado.db "SELECT estrategia, COUNT(*) velas, SUM(direcao!=0) sinais FROM observacoes GROUP BY estrategia;"
```

## 5. Backup automático

O disco da VM é persistente, mas VM não é backup: instância removida leva o
disco junto. Uma cópia diária resolve.

```bash
chmod +x ~/analise-cripto/scripts/backup.sh
( crontab -l 2>/dev/null; echo "0 3 * * * $HOME/analise-cripto/scripts/backup.sh >> $HOME/backup.log 2>&1" ) | crontab -
```

O script usa `sqlite3 .backup` em vez de `cp` — copiar um SQLite enquanto ele
está sendo escrito gera um arquivo corrompido que só acusa erro na hora de ler,
semanas depois.

## 6. Trazer os dados para analisar

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
