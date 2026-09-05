"""Prova que o motor opera como um grafico em movimento.

A duvida e legitima: o motor recebe a serie inteira de uma vez. Como saber que
ele nao esta espiando o futuro em algum canto?

A resposta nao pode ser "confia". Aqui a serie e entregue **vela a vela**, como
aconteceria ao vivo - a cada passo a estrategia so ve o que ja fechou -, e o
resultado e comparado com o do motor rodando de uma vez sobre tudo.

Se os dois baterem, a forma de entregar os dados nao importa: o sistema so usa
passado. Se divergirem, ha vazamento em algum lugar, e este teste diz onde.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from nucleo.backtest.motor import ConfigExecucao, ModeloCustos, executar
from nucleo.estrategias.cruzamento_ema import EstrategiaCruzamentoEma
from nucleo.estrategias.rsi_macd import EstrategiaRsiMacd
from nucleo.estrategias.ruptura import EstrategiaRupturaDonchian

SEM_CUSTO = ModeloCustos(taxa_por_lado=0.0, slippage_por_lado=0.0)


@pytest.fixture
def mercado() -> pd.DataFrame:
    gerador = np.random.default_rng(2024)
    fechamento = 100 + gerador.normal(0, 1.2, 700).cumsum()
    amplitude = np.abs(gerador.normal(0, 0.6, 700)) + 0.05
    return pd.DataFrame(
        {
            "abertura": np.r_[fechamento[0], fechamento[:-1]],
            "maxima": np.maximum(fechamento, np.r_[fechamento[0], fechamento[:-1]]) + amplitude,
            "minima": np.minimum(fechamento, np.r_[fechamento[0], fechamento[:-1]]) - amplitude,
            "fechamento": fechamento,
            "volume": np.abs(gerador.normal(100, 20, 700)) + 1,
        },
        index=pd.date_range("2025-01-01", periods=700, freq="1h", tz="UTC"),
    )


def sinais_em_tempo_real(estrategia, quadro: pd.DataFrame) -> pd.DataFrame:
    """Reconstroi os sinais entregando uma vela por vez.

    Em cada passo a estrategia recebe apenas o que ja teria fechado naquele
    instante, e so a decisao da ULTIMA vela e guardada - exatamente o que
    acontece no coletor ao vivo.
    """
    decisoes = []
    for corte in range(1, len(quadro) + 1):
        parcial = quadro.iloc[:corte]
        decisoes.append(estrategia.gerar_sinais(parcial).iloc[-1])
    return pd.DataFrame(decisoes, index=quadro.index)


@pytest.mark.parametrize(
    "fabrica",
    [EstrategiaRsiMacd, EstrategiaCruzamentoEma, EstrategiaRupturaDonchian],
    ids=["rsi_macd", "ema", "donchian"],
)
def test_vela_a_vela_da_o_mesmo_que_de_uma_vez(mercado, fabrica):
    estrategia = fabrica()
    # Uma janela menor mantem o teste rapido: sao centenas de recalculos.
    quadro = mercado.iloc[-320:]

    de_uma_vez = estrategia.gerar_sinais(quadro)
    ao_vivo = sinais_em_tempo_real(estrategia, quadro)

    pd.testing.assert_series_equal(
        ao_vivo["direcao"].astype("int8"),
        de_uma_vez["direcao"].astype("int8"),
        check_names=False,
    )
    for coluna in ("stop", "alvo"):
        pd.testing.assert_series_equal(
            ao_vivo[coluna].astype(float),
            de_uma_vez[coluna].astype(float),
            check_names=False,
        )


def test_resultado_financeiro_e_o_mesmo(mercado):
    """Nao basta o sinal bater: o dinheiro tambem tem que bater."""
    estrategia = EstrategiaRupturaDonchian()
    quadro = mercado.iloc[-320:]
    config = ConfigExecucao(max_barras_no_trade=48)

    resultado_lote = executar(quadro, estrategia.gerar_sinais(quadro), SEM_CUSTO, config)
    resultado_vivo = executar(
        quadro, sinais_em_tempo_real(estrategia, quadro), SEM_CUSTO, config
    )

    assert len(resultado_lote.trades) == len(resultado_vivo.trades)
    if not resultado_lote.trades.empty:
        pd.testing.assert_series_equal(
            resultado_lote.trades["retorno_liquido_pct"],
            resultado_vivo.trades["retorno_liquido_pct"],
        )
