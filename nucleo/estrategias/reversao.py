"""Setups de reversao - apostar que o preco volta para a referencia.

Duas referencias diferentes, e a diferenca entre elas e o que separa os dois
setups:

- **Banda de Bollinger**: a referencia e a media dos fechamentos. Mercado que
  se afasta demais da propria media tende a voltar - desde que nao esteja em
  tendencia, dai o filtro de ADX. Sem esse filtro, comprar o extremo inferior
  em queda forte e so pegar faca caindo.
- **VWAP**: a referencia e o preco medio ponderado por volume. E o benchmark
  pelo qual mesa institucional e avaliada, entao os desvios em torno dele nao
  sao linha arbitraria - sao onde ordem grande costuma aparecer, porque quem
  precisa comprar quer comprar abaixo do VWAP.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

from ..indicadores import basicos as ind
from .base import (
    COMPRA,
    NEUTRO,
    VENDA,
    Estrategia,
    apenas_transicoes,
    descartar_protecao_invalida,
    quadro_sinais,
    validar_sinais,
)


@dataclass(frozen=True)
class ParametrosReversaoBanda:
    periodo_bollinger: int = 20
    desvios: float = 2.0
    periodo_adx: int = 14
    adx_maximo: float = 20.0
    periodo_atr: int = 14
    folga_stop_atr: float = 1.0
    modo: str = "evento"

    def com(self, **mudancas) -> "ParametrosReversaoBanda":
        return replace(self, **mudancas)


class EstrategiaReversaoBanda(Estrategia):
    """Compra no extremo inferior da banda, mas so em mercado sem tendencia."""

    def __init__(self, parametros: ParametrosReversaoBanda | None = None) -> None:
        self.p = parametros or ParametrosReversaoBanda()
        self.nome = f"reversao_bb({self.p.periodo_bollinger},adx<{self.p.adx_maximo:g})"

    def barras_de_aquecimento(self) -> int:
        return max(self.p.periodo_bollinger, self.p.periodo_adx * 5, self.p.periodo_atr * 5) + 30

    def painel_indicadores(self, quadro: pd.DataFrame) -> pd.DataFrame:
        p = self.p
        fechamento = quadro["fechamento"]
        bandas = ind.bandas_bollinger(fechamento, p.periodo_bollinger, p.desvios)
        return pd.DataFrame(
            {
                "bb_inferior": bandas["inferior"],
                "bb_media": bandas["media"],
                "bb_superior": bandas["superior"],
                "bb_largura": bandas["largura"],
                "adx": ind.indice_direcional_medio(
                    quadro["maxima"], quadro["minima"], fechamento, p.periodo_adx
                )["adx"],
                "atr": ind.faixa_verdadeira_media(
                    quadro["maxima"], quadro["minima"], fechamento, p.periodo_atr
                ),
            }
        )

    def gerar_sinais(self, quadro: pd.DataFrame) -> pd.DataFrame:
        p = self.p
        fechamento = quadro["fechamento"]
        painel = self.painel_indicadores(quadro)

        pronto = painel["bb_inferior"].notna() & painel["adx"].notna() & painel["atr"].notna()

        # ADX baixo = sem tendencia definida. Reversao a media so tem sentido
        # nesse regime; em tendencia forte o extremo da banda nao e exagero, e
        # o comeco do movimento.
        lateral = painel["adx"] < p.adx_maximo

        direcao = pd.Series(
            np.select(
                [
                    pronto & lateral & (fechamento < painel["bb_inferior"]),
                    pronto & lateral & (fechamento > painel["bb_superior"]),
                ],
                [COMPRA, VENDA],
                default=NEUTRO,
            ),
            index=quadro.index,
            dtype="int8",
        )
        if p.modo == "evento":
            direcao = apenas_transicoes(direcao)

        sinais = quadro_sinais(quadro.index)
        sinais["direcao"] = direcao

        # Quanto mais fora da banda, mais esticado - e mais forte o sinal.
        excesso = (fechamento - painel["bb_media"]).abs() / (
            painel["bb_superior"] - painel["bb_media"]
        )
        sinais["forca"] = np.where(
            direcao == NEUTRO, 0.0, np.clip(excesso.fillna(0) / 1.5, 0.1, 1.0)
        )

        folga = painel["atr"] * p.folga_stop_atr
        comprando, vendendo = direcao == COMPRA, direcao == VENDA
        # O alvo e a media: reversao a media termina na media, nao no outro lado.
        sinais.loc[comprando, "stop"] = (painel["bb_inferior"] - folga)[comprando]
        sinais.loc[comprando, "alvo"] = painel["bb_media"][comprando]
        sinais.loc[vendendo, "stop"] = (painel["bb_superior"] + folga)[vendendo]
        sinais.loc[vendendo, "alvo"] = painel["bb_media"][vendendo]
        sinais.loc[direcao != NEUTRO, "motivo"] = "extremo de banda em mercado lateral"

        sinais = descartar_protecao_invalida(sinais, fechamento)
        return validar_sinais(sinais, quadro.index)


@dataclass(frozen=True)
class ParametrosVwap:
    desvios_entrada: float = 2.0
    desvios_stop: float = 3.0
    ancora: str = "W"
    barras_minimas: int = 8
    periodo_atr: int = 14
    modo: str = "evento"

    def com(self, **mudancas) -> "ParametrosVwap":
        return replace(self, **mudancas)


class EstrategiaDesvioVwap(Estrategia):
    """Compra abaixo do VWAP ancorado, mira a volta para ele."""

    def __init__(self, parametros: ParametrosVwap | None = None) -> None:
        self.p = parametros or ParametrosVwap()
        self.nome = f"vwap({self.p.ancora},{self.p.desvios_entrada:g}sd)"

    def barras_de_aquecimento(self) -> int:
        return max(self.p.periodo_atr * 5, self.p.barras_minimas * 4) + 40

    def painel_indicadores(self, quadro: pd.DataFrame) -> pd.DataFrame:
        p = self.p
        referencia = ind.vwap_sessao(quadro, p.desvios_entrada, p.ancora)
        return pd.DataFrame(
            {
                "vwap": referencia["vwap"],
                "vwap_desvio": referencia["desvio"],
                "vwap_inferior": referencia["inferior"],
                "vwap_superior": referencia["superior"],
                "atr": ind.faixa_verdadeira_media(
                    quadro["maxima"], quadro["minima"], quadro["fechamento"], p.periodo_atr
                ),
            }
        )

    def gerar_sinais(self, quadro: pd.DataFrame) -> pd.DataFrame:
        p = self.p
        fechamento = quadro["fechamento"]
        painel = self.painel_indicadores(quadro)

        # Nas primeiras barras da ancora o VWAP tem poucos pontos e o desvio e
        # praticamente zero - qualquer oscilacao viraria "3 sigma". Esperar a
        # referencia se formar e a diferenca entre sinal e ruido.
        ordem_na_ancora = quadro.groupby(
            ind.rotulo_ancora(quadro.index, p.ancora)
        ).cumcount()
        maduro = ordem_na_ancora >= p.barras_minimas

        pronto = (
            painel["vwap"].notna()
            & painel["atr"].notna()
            & (painel["vwap_desvio"] > 0)
            & maduro
        )

        direcao = pd.Series(
            np.select(
                [
                    pronto & (fechamento < painel["vwap_inferior"]),
                    pronto & (fechamento > painel["vwap_superior"]),
                ],
                [COMPRA, VENDA],
                default=NEUTRO,
            ),
            index=quadro.index,
            dtype="int8",
        )
        if p.modo == "evento":
            direcao = apenas_transicoes(direcao)

        sinais = quadro_sinais(quadro.index)
        sinais["direcao"] = direcao

        distancia = (fechamento - painel["vwap"]).abs() / painel["vwap_desvio"]
        sinais["forca"] = np.where(
            direcao == NEUTRO, 0.0, np.clip(distancia.fillna(0) / 4.0, 0.1, 1.0)
        )

        stop_distancia = painel["vwap_desvio"] * p.desvios_stop
        comprando, vendendo = direcao == COMPRA, direcao == VENDA
        sinais.loc[comprando, "stop"] = (painel["vwap"] - stop_distancia)[comprando]
        sinais.loc[comprando, "alvo"] = painel["vwap"][comprando]
        sinais.loc[vendendo, "stop"] = (painel["vwap"] + stop_distancia)[vendendo]
        sinais.loc[vendendo, "alvo"] = painel["vwap"][vendendo]
        sinais.loc[direcao != NEUTRO, "motivo"] = "preco esticado em relacao ao vwap"

        sinais = descartar_protecao_invalida(sinais, fechamento)
        return validar_sinais(sinais, quadro.index)
