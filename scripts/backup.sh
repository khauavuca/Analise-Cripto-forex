#!/usr/bin/env bash
# Copia de seguranca do banco de coleta.
#
# Usa `sqlite3 .backup` e nao `cp`. A diferenca importa: o coletor escreve o
# tempo todo, e copiar o arquivo enquanto uma transacao esta aberta produz um
# banco corrompido que so da erro na hora de ler - semanas depois, quando voce
# for analisar. O `.backup` faz a copia de forma consistente com o banco vivo.
set -euo pipefail

BANCO="${BANCO:-$HOME/analise-cripto/dados/dados_mercado.db}"
DESTINO="${DESTINO:-$HOME/analise-cripto/dados/backups}"
MANTER="${MANTER:-14}"

if [ ! -f "$BANCO" ]; then
    echo "Banco nao encontrado: $BANCO" >&2
    exit 1
fi

mkdir -p "$DESTINO"
CARIMBO="$(date -u +%Y%m%d-%H%M)"
ARQUIVO="$DESTINO/dados-$CARIMBO.db"

sqlite3 "$BANCO" ".backup '$ARQUIVO'"
gzip -f "$ARQUIVO"

# Mantem apenas as copias mais recentes; sem isso o disco da VM enche.
ls -1t "$DESTINO"/dados-*.db.gz 2>/dev/null | tail -n "+$((MANTER + 1))" | xargs -r rm --

echo "Backup em $ARQUIVO.gz ($(du -h "$ARQUIVO.gz" | cut -f1))"
