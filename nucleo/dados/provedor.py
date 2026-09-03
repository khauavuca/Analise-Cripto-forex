"""Contrato de acesso a dados de mercado.

Cumpre o papel do HistoryProvider do LEAN: quem consome nao sabe de onde a
vela veio, apenas que ela chega normalizada. Trocar de corretora - ou plugar
forex mais tarde - e implementar esta interface, sem tocar na analise.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone

import pandas as pd

COLUNAS_VELA = ["abertura", "maxima", "minima", "fechamento", "volume"]

DURACAO_MS = {
    "1m": 60_000,
    "5m": 5 * 60_000,
    "15m": 15 * 60_000,
    "30m": 30 * 60_000,
    "1h": 60 * 60_000,
    "2h": 2 * 60 * 60_000,
    "4h": 4 * 60 * 60_000,
    "6h": 6 * 60 * 60_000,
    "12h": 12 * 60 * 60_000,
    "1d": 24 * 60 * 60_000,
}

REGRA_PANDAS = {
    "1m": "1min", "5m": "5min", "15m": "15min", "30m": "30min",
    "1h": "1h", "2h": "2h", "4h": "4h", "6h": "6h", "12h": "12h", "1d": "1D",
}

AGREGACAO = {
    "abertura": "first",
    "maxima": "max",
    "minima": "min",
    "fechamento": "last",
    "volume": "sum",
}


class TimeframeInvalido(ValueError):
    """Timeframe fora da tabela suportada, ou incompativel com a origem."""


def duracao_ms(timeframe: str) -> int:
    try:
        return DURACAO_MS[timeframe]
    except KeyError:
        raise TimeframeInvalido(
            f"Timeframe {timeframe!r} nao suportado. Use um de: {', '.join(DURACAO_MS)}"
        ) from None


def agora_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def para_ms(momento: datetime | str | int | None) -> int | None:
    """Converte data, texto ISO ou epoch para milissegundos UTC."""
    if momento is None:
        return None
    if isinstance(momento, int):
        return momento
    if isinstance(momento, str):
        momento = datetime.fromisoformat(momento)
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=timezone.utc)
    return int(momento.timestamp() * 1000)


def quadro_vazio() -> pd.DataFrame:
    indice = pd.DatetimeIndex([], tz="UTC", name="data_hora")
    return pd.DataFrame(
        {coluna: pd.Series(dtype="float64") for coluna in COLUNAS_VELA}, index=indice
    )


def normalizar(quadro: pd.DataFrame) -> pd.DataFrame:
    """Deixa o quadro no formato canonico: UTC, ordenado, sem duplicatas."""
    if quadro is None or quadro.empty:
        return quadro_vazio()

    quadro = quadro.loc[:, COLUNAS_VELA].astype("float64")
    quadro = quadro[~quadro.index.duplicated(keep="last")].sort_index()
    quadro.index.name = "data_hora"
    return quadro.dropna()


def descartar_vela_aberta(
    quadro: pd.DataFrame, timeframe: str, referencia_ms: int | None = None
) -> pd.DataFrame:
    """Remove do fim as velas que ainda nao fecharam.

    A ultima vela devolvida por qualquer corretora e a vela corrente, ainda em
    formacao - o fechamento dela ainda vai mudar. Deixar essa vela entrar na
    estrategia e look-ahead disfarcado, entao ela sai antes de qualquer calculo.
    """
    if quadro.empty:
        return quadro

    referencia = agora_ms() if referencia_ms is None else referencia_ms
    ultimo_fechado = referencia - duracao_ms(timeframe)
    return quadro[quadro.index <= pd.Timestamp(ultimo_fechado, unit="ms", tz="UTC")]


def reamostrar(
    quadro: pd.DataFrame, timeframe_origem: str, timeframe_destino: str
) -> pd.DataFrame:
    """Agrega velas para um timeframe maior, descartando o balde incompleto.

    Serve para derivar 4h a partir de 1h quando a fonte nao oferece o
    timeframe nativamente. O ultimo balde so sobrevive se os dados de origem
    cobrirem o periodo inteiro dele - caso contrario seria uma vela pela
    metade se passando por fechada.
    """
    origem = duracao_ms(timeframe_origem)
    destino = duracao_ms(timeframe_destino)

    if destino < origem:
        raise TimeframeInvalido(
            f"Nao da para reamostrar de {timeframe_origem} para {timeframe_destino}: "
            "o destino precisa ser maior ou igual a origem."
        )
    if destino % origem:
        raise TimeframeInvalido(
            f"{timeframe_destino} nao e multiplo inteiro de {timeframe_origem}; "
            "a agregacao ficaria desalinhada."
        )
    if destino == origem or quadro.empty:
        return normalizar(quadro)

    # origin="epoch" nao e detalhe: com o default ("start") a grade de 4h se
    # desloca conforme a primeira vela da fatia pedida, e o mesmo backtest
    # passa a dar numeros diferentes so por causa da data de inicio.
    agrupado = quadro.resample(
        REGRA_PANDAS[timeframe_destino], label="left", closed="left", origin="epoch"
    )
    agregado = agrupado.agg(AGREGACAO)

    # Um balde so sobrevive se tiver TODAS as velas de origem que deveria ter.
    # Contar e mais seguro que dropna(): uma vela de 4h montada com uma unica
    # vela de 1h nao tem NaN nenhum - ela so esta errada. Este filtro derruba
    # de uma vez o balde do fim ainda em formacao e qualquer buraco de
    # indisponibilidade da corretora no meio da serie.
    esperadas = destino // origem
    agregado = agregado[agrupado.size() == esperadas]

    return normalizar(agregado)


class ContratoQuebrado(ValueError):
    """O quadro nao respeita o formato canonico de velas."""


def validar_velas(quadro: pd.DataFrame, contexto: str = "") -> None:
    """Estoura se o quadro estiver malformado, em vez de deixar passar.

    Existe por causa de um bug real do conector antigo: ele montava o
    DataFrame com nomes de coluna que nao batiam com o payload da corretora,
    e o pandas devolvia um quadro com o numero certo de linhas e tudo NaN.
    As checagens de tamanho passavam, os indicadores viravam NaN, toda
    comparacao com NaN da False - e o sistema simplesmente nunca emitia
    sinal, sem erro nenhum. Silencio e o pior modo de falha possivel aqui.
    """
    onde = f" ({contexto})" if contexto else ""

    faltando = [coluna for coluna in COLUNAS_VELA if coluna not in quadro.columns]
    if faltando:
        raise ContratoQuebrado(f"Colunas ausentes{onde}: {faltando}")

    if quadro.empty:
        return

    if not isinstance(quadro.index, pd.DatetimeIndex) or quadro.index.tz is None:
        raise ContratoQuebrado(f"O indice precisa ser DatetimeIndex em UTC{onde}.")
    if not quadro.index.is_monotonic_increasing:
        raise ContratoQuebrado(f"O indice nao esta ordenado{onde}.")
    if quadro.index.has_duplicates:
        raise ContratoQuebrado(f"Ha timestamps repetidos{onde}.")

    nulos = quadro[COLUNAS_VELA].isna().sum()
    if nulos.any():
        raise ContratoQuebrado(f"Ha valores nulos{onde}: {nulos[nulos > 0].to_dict()}")

    topo = quadro[["abertura", "fechamento"]].max(axis=1)
    base = quadro[["abertura", "fechamento"]].min(axis=1)
    if (quadro["maxima"] < topo).any():
        raise ContratoQuebrado(f"Ha velas com maxima abaixo do corpo{onde}.")
    if (quadro["minima"] > base).any():
        raise ContratoQuebrado(f"Ha velas com minima acima do corpo{onde}.")


class ProvedorDados(ABC):
    """Fonte de velas e precos. Implemente para plugar outra corretora."""

    nome: str = "abstrato"

    @abstractmethod
    def obter_velas(
        self,
        par: str,
        timeframe: str,
        inicio: datetime | str | int | None = None,
        fim: datetime | str | int | None = None,
    ) -> pd.DataFrame:
        """Velas fechadas no intervalo, ja normalizadas."""

    @abstractmethod
    def obter_preco(self, par: str) -> float:
        """Ultimo preco negociado."""
