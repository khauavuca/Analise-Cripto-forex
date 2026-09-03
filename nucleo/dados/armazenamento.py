"""Cache local de velas e registro de resultados, em SQLite.

Duas razoes para existir:

1. Backtest tem que rodar offline. Se cada execucao for buscar dados na rede,
   uma falha parcial encurta a janela em silencio e as metricas mudam sem
   ninguem perceber - o resultado deixa de ser reproduzivel.
2. `resultados_sinal` e a tabela que o projeto nunca teve. Sem historico de
   trade fechado nao existe conversa sobre assertividade.

Nao reaproveita o `database.py` antigo de proposito: la o Fernet e instanciado
em nivel de modulo, entao um simples `import database` grava um arquivo de
chave no disco como efeito colateral. Velas sao dado publico e precisam de
varredura por faixa - criptografar mataria justamente isso.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime

import pandas as pd

from .provedor import COLUNAS_VELA, normalizar, para_ms, quadro_vazio, validar_velas

ESQUEMA = """
CREATE TABLE IF NOT EXISTS velas (
    corretora   TEXT    NOT NULL,
    par         TEXT    NOT NULL,
    timeframe   TEXT    NOT NULL,
    abertura_ms INTEGER NOT NULL,
    abertura    REAL    NOT NULL,
    maxima      REAL    NOT NULL,
    minima      REAL    NOT NULL,
    fechamento  REAL    NOT NULL,
    volume      REAL    NOT NULL,
    PRIMARY KEY (corretora, par, timeframe, abertura_ms)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS cobertura (
    corretora TEXT    NOT NULL,
    par       TEXT    NOT NULL,
    timeframe TEXT    NOT NULL,
    inicio_ms INTEGER NOT NULL,
    fim_ms    INTEGER NOT NULL,
    PRIMARY KEY (corretora, par, timeframe, inicio_ms)
);

CREATE TABLE IF NOT EXISTS execucoes (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    rodado_em     TEXT NOT NULL,
    estrategia    TEXT NOT NULL,
    par           TEXT NOT NULL,
    timeframe     TEXT NOT NULL,
    inicio        TEXT,
    fim           TEXT,
    parametros    TEXT,
    custos        TEXT,
    metricas      TEXT
);

CREATE TABLE IF NOT EXISTS resultados_sinal (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    execucao_id         INTEGER REFERENCES execucoes(id),
    estrategia          TEXT    NOT NULL,
    par                 TEXT    NOT NULL,
    timeframe           TEXT    NOT NULL,
    direcao             INTEGER NOT NULL,
    entrada_ms          INTEGER NOT NULL,
    preco_entrada       REAL    NOT NULL,
    saida_ms            INTEGER NOT NULL,
    preco_saida         REAL    NOT NULL,
    motivo_saida        TEXT    NOT NULL,
    barras_no_trade     INTEGER NOT NULL,
    retorno_bruto_pct   REAL    NOT NULL,
    retorno_liquido_pct REAL    NOT NULL,
    multiplo_r          REAL,
    mfe_pct             REAL,
    mae_pct             REAL,
    ambiguo             INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_resultados_execucao
    ON resultados_sinal (execucao_id);
"""

_COLUNAS_INSERCAO = (
    "corretora, par, timeframe, abertura_ms, "
    "abertura, maxima, minima, fechamento, volume"
)


class Armazenamento:
    """Acesso ao banco local. Use como context manager."""

    def __init__(self, caminho: str | None = None) -> None:
        self.caminho = caminho or os.getenv("BANCO_DADOS", "dados_mercado.db")
        self.conexao = sqlite3.connect(self.caminho)
        self.conexao.execute("PRAGMA journal_mode=WAL")
        self.conexao.execute("PRAGMA synchronous=NORMAL")
        self.conexao.executescript(ESQUEMA)
        self.conexao.commit()

    def __enter__(self) -> "Armazenamento":
        return self

    def __exit__(self, *_) -> None:
        self.fechar()

    def fechar(self) -> None:
        self.conexao.close()

    # ------------------------------------------------------------------ velas

    def salvar_velas(
        self, corretora: str, par: str, timeframe: str, quadro: pd.DataFrame
    ) -> int:
        """Grava velas. Regravar o mesmo periodo e barato e nao duplica."""
        if quadro.empty:
            return 0

        validar_velas(quadro, f"gravacao {corretora} {par} {timeframe}")
        linhas = [
            (
                corretora,
                par,
                timeframe,
                int(momento.timestamp() * 1000),
                float(vela.abertura),
                float(vela.maxima),
                float(vela.minima),
                float(vela.fechamento),
                float(vela.volume),
            )
            for momento, vela in quadro.iterrows()
        ]
        with self.conexao:
            self.conexao.executemany(
                f"INSERT OR REPLACE INTO velas ({_COLUNAS_INSERCAO}) "
                f"VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                linhas,
            )
        return len(linhas)

    def carregar_velas(
        self,
        corretora: str,
        par: str,
        timeframe: str,
        inicio: datetime | str | int | None = None,
        fim: datetime | str | int | None = None,
    ) -> pd.DataFrame:
        consulta = (
            "SELECT abertura_ms, abertura, maxima, minima, fechamento, volume "
            "FROM velas WHERE corretora = ? AND par = ? AND timeframe = ?"
        )
        parametros: list = [corretora, par, timeframe]

        inicio_ms, fim_ms = para_ms(inicio), para_ms(fim)
        if inicio_ms is not None:
            consulta += " AND abertura_ms >= ?"
            parametros.append(inicio_ms)
        if fim_ms is not None:
            consulta += " AND abertura_ms <= ?"
            parametros.append(fim_ms)
        consulta += " ORDER BY abertura_ms"

        quadro = pd.read_sql_query(consulta, self.conexao, params=parametros)
        if quadro.empty:
            return quadro_vazio()

        quadro.index = pd.to_datetime(quadro.pop("abertura_ms"), unit="ms", utc=True)
        return normalizar(quadro)

    def contar_velas(self, corretora: str, par: str, timeframe: str) -> int:
        cursor = self.conexao.execute(
            "SELECT COUNT(*) FROM velas WHERE corretora=? AND par=? AND timeframe=?",
            (corretora, par, timeframe),
        )
        return int(cursor.fetchone()[0])

    # -------------------------------------------------------------- cobertura

    def registrar_cobertura(
        self, corretora: str, par: str, timeframe: str, inicio_ms: int, fim_ms: int
    ) -> None:
        """Anota que este intervalo ja foi consultado na corretora.

        Sem esse registro nao da para distinguir "nunca baixei" de "baixei e a
        corretora nao tem nada aqui" - e o segundo caso viraria uma consulta de
        rede repetida para sempre.
        """
        with self.conexao:
            self.conexao.execute(
                "INSERT OR REPLACE INTO cobertura VALUES (?, ?, ?, ?, ?)",
                (corretora, par, timeframe, int(inicio_ms), int(fim_ms)),
            )

    def cobertura(
        self, corretora: str, par: str, timeframe: str
    ) -> list[tuple[int, int]]:
        """Intervalos ja baixados, unidos quando se encostam."""
        cursor = self.conexao.execute(
            "SELECT inicio_ms, fim_ms FROM cobertura "
            "WHERE corretora=? AND par=? AND timeframe=? ORDER BY inicio_ms",
            (corretora, par, timeframe),
        )
        return unir_intervalos([(int(a), int(b)) for a, b in cursor.fetchall()])

    def faixas_faltantes(
        self, corretora: str, par: str, timeframe: str, inicio_ms: int, fim_ms: int
    ) -> list[tuple[int, int]]:
        """O que ainda precisa ser buscado na rede."""
        return subtrair_intervalos(
            (inicio_ms, fim_ms), self.cobertura(corretora, par, timeframe)
        )

    # -------------------------------------------------------------- execucoes

    def registrar_execucao(
        self,
        estrategia: str,
        par: str,
        timeframe: str,
        inicio: str | None,
        fim: str | None,
        parametros: str,
        custos: str,
        metricas: str,
    ) -> int:
        with self.conexao:
            cursor = self.conexao.execute(
                "INSERT INTO execucoes (rodado_em, estrategia, par, timeframe, "
                "inicio, fim, parametros, custos, metricas) "
                "VALUES (datetime('now'), ?, ?, ?, ?, ?, ?, ?, ?)",
                (estrategia, par, timeframe, inicio, fim, parametros, custos, metricas),
            )
        return int(cursor.lastrowid)

    def registrar_trades(
        self,
        execucao_id: int,
        estrategia: str,
        par: str,
        timeframe: str,
        trades: pd.DataFrame,
    ) -> int:
        if trades.empty:
            return 0

        linhas = [
            (
                execucao_id,
                estrategia,
                par,
                timeframe,
                int(t.direcao),
                int(t.entrada.timestamp() * 1000),
                float(t.preco_entrada),
                int(t.saida.timestamp() * 1000),
                float(t.preco_saida),
                str(t.motivo_saida),
                int(t.barras_no_trade),
                float(t.retorno_bruto_pct),
                float(t.retorno_liquido_pct),
                None if pd.isna(t.multiplo_r) else float(t.multiplo_r),
                None if pd.isna(t.mfe_pct) else float(t.mfe_pct),
                None if pd.isna(t.mae_pct) else float(t.mae_pct),
                int(bool(t.ambiguo)),
            )
            for t in trades.itertuples()
        ]
        with self.conexao:
            self.conexao.executemany(
                "INSERT INTO resultados_sinal (execucao_id, estrategia, par, "
                "timeframe, direcao, entrada_ms, preco_entrada, saida_ms, "
                "preco_saida, motivo_saida, barras_no_trade, retorno_bruto_pct, "
                "retorno_liquido_pct, multiplo_r, mfe_pct, mae_pct, ambiguo) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                linhas,
            )
        return len(linhas)


def unir_intervalos(intervalos: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Funde intervalos que se sobrepoem ou se tocam."""
    if not intervalos:
        return []

    unidos: list[tuple[int, int]] = []
    for inicio, fim in sorted(intervalos):
        if unidos and inicio <= unidos[-1][1]:
            anterior_inicio, anterior_fim = unidos[-1]
            unidos[-1] = (anterior_inicio, max(anterior_fim, fim))
        else:
            unidos.append((inicio, fim))
    return unidos


def subtrair_intervalos(
    alvo: tuple[int, int], cobertos: list[tuple[int, int]]
) -> list[tuple[int, int]]:
    """Pedacos de `alvo` que nenhum intervalo de `cobertos` alcanca."""
    inicio, fim = alvo
    if inicio >= fim:
        return []

    faltantes: list[tuple[int, int]] = []
    cursor = inicio
    for coberto_inicio, coberto_fim in unir_intervalos(cobertos):
        if coberto_fim <= cursor:
            continue
        if coberto_inicio >= fim:
            break
        if coberto_inicio > cursor:
            faltantes.append((cursor, min(coberto_inicio, fim)))
        cursor = max(cursor, coberto_fim)
        if cursor >= fim:
            break

    if cursor < fim:
        faltantes.append((cursor, fim))
    return faltantes
