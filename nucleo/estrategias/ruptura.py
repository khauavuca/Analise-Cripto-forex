"""Setups de ruptura - a familia que sustenta a industria de managed futures.

Duas abordagens diferentes para a mesma ideia de que preco que sai de uma faixa
tende a continuar:

- **Donchian**: rompe a maxima de N barras. E o sistema das Tartarugas de
  Richard Dennis, e continua sendo o esqueleto de boa parte dos CTAs.
- **Compressao de volatilidade**: nao espera o rompimento de preco, espera o
  mercado ficar anormalmente parado e entra quando ele volta a se mexer. E o
  squeeze de John Carter, padrao em mesa de prop.

Uma diferenca honesta em relacao ao original: as Tartarugas saiam pelo canal
oposto de 10 barras, nao por alvo fixo. O motor daqui trabalha com stop e alvo,
entao a saida virou multiplo de ATR mais prazo maximo. E uma aproximacao, nao o
sistema original.
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
class ParametrosDonchian:
    periodo_canal: int = 20
    periodo_atr: int = 20
    multiplo_stop: float = 2.0
    multiplo_alvo: float = 4.0
    periodo_filtro: int = 100
    usar_filtro: bool = True
    modo: str = "evento"

    def com(self, **mudancas) -> "ParametrosDonchian":
        return replace(self, **mudancas)


class EstrategiaRupturaDonchian(Estrategia):
    """Compra o rompimento da maxima de N barras, vende o da minima."""

    def __init__(self, parametros: ParametrosDonchian | None = None) -> None:
        self.p = parametros or ParametrosDonchian()
        self.nome = f"donchian({self.p.periodo_canal},{self.p.multiplo_stop:g}atr)"

    def barras_de_aquecimento(self) -> int:
        return max(self.p.periodo_canal, self.p.periodo_filtro, self.p.periodo_atr * 5) + 20

    def painel_indicadores(self, quadro: pd.DataFrame) -> pd.DataFrame:
        p = self.p
        fechamento = quadro["fechamento"]
        canal = ind.canal_donchian(quadro["maxima"], quadro["minima"], p.periodo_canal)
        return pd.DataFrame(
            {
                "canal_alto": canal["resistencia"],
                "canal_baixo": canal["suporte"],
                "atr": ind.faixa_verdadeira_media(
                    quadro["maxima"], quadro["minima"], fechamento, p.periodo_atr
                ),
                "filtro": ind.media_movel_simples(fechamento, p.periodo_filtro),
            }
        )

    def gerar_sinais(self, quadro: pd.DataFrame) -> pd.DataFrame:
        p = self.p
        fechamento = quadro["fechamento"]
        painel = self.painel_indicadores(quadro)

        pronto = (
            painel["canal_alto"].notna()
            & painel["atr"].notna()
            & (painel["filtro"].notna() if p.usar_filtro else True)
        )

        # O canal ja vem deslocado uma barra, entao romper "a maxima das 20
        # anteriores" nao inclui a propria vela que esta rompendo.
        rompeu_cima = fechamento > painel["canal_alto"]
        rompeu_baixo = fechamento < painel["canal_baixo"]

        if p.usar_filtro:
            # Filtro de regime: comprar rompimento contra a tendencia de fundo e
            # o jeito mais rapido de colecionar rompimento falso.
            rompeu_cima = rompeu_cima & (fechamento > painel["filtro"])
            rompeu_baixo = rompeu_baixo & (fechamento < painel["filtro"])

        direcao = pd.Series(
            np.select(
                [pronto & rompeu_cima, pronto & rompeu_baixo],
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

        # Confianca pela distancia do rompimento em ATR: rompimento raspando a
        # borda vale menos que rompimento com folga.
        folga = (fechamento - painel["canal_alto"]).abs() / painel["atr"]
        sinais["forca"] = np.where(
            direcao == NEUTRO, 0.0, np.clip(folga.fillna(0) / 2.0, 0.1, 1.0)
        )

        stop = painel["atr"] * p.multiplo_stop
        alvo = painel["atr"] * p.multiplo_alvo
        comprando, vendendo = direcao == COMPRA, direcao == VENDA
        sinais.loc[comprando, "stop"] = (fechamento - stop)[comprando]
        sinais.loc[comprando, "alvo"] = (fechamento + alvo)[comprando]
        sinais.loc[vendendo, "stop"] = (fechamento + stop)[vendendo]
        sinais.loc[vendendo, "alvo"] = (fechamento - alvo)[vendendo]
        sinais.loc[direcao != NEUTRO, "motivo"] = "rompimento de canal com tendencia"

        sinais = descartar_protecao_invalida(sinais, fechamento)
        return validar_sinais(sinais, quadro.index)


@dataclass(frozen=True)
class ParametrosCompressao:
    periodo_bollinger: int = 20
    desvios_bollinger: float = 2.0
    periodo_keltner: int = 20
    multiplo_keltner: float = 1.5
    periodo_atr: int = 14
    periodo_momento: int = 20
    multiplo_stop: float = 1.5
    multiplo_alvo: float = 3.0

    def com(self, **mudancas) -> "ParametrosCompressao":
        return replace(self, **mudancas)


class EstrategiaCompressaoVolatilidade(Estrategia):
    """Entra quando a volatilidade comprimida se solta.

    Bollinger mede desvio de fechamentos, Keltner mede amplitude real das
    velas. Quando as bandas de Bollinger encolhem para dentro das de Keltner, o
    mercado esta parado para o padrao dele mesmo - e mercado parado nao fica
    parado. A entrada e no momento em que a compressao se desfaz, na direcao do
    momento acumulado.
    """

    def __init__(self, parametros: ParametrosCompressao | None = None) -> None:
        self.p = parametros or ParametrosCompressao()
        self.nome = f"compressao({self.p.periodo_bollinger}/{self.p.multiplo_keltner:g})"

    def barras_de_aquecimento(self) -> int:
        return (
            max(
                self.p.periodo_bollinger,
                self.p.periodo_keltner,
                self.p.periodo_momento,
                self.p.periodo_atr * 5,
            )
            + 30
        )

    def painel_indicadores(self, quadro: pd.DataFrame) -> pd.DataFrame:
        p = self.p
        fechamento = quadro["fechamento"]
        bollinger = ind.bandas_bollinger(fechamento, p.periodo_bollinger, p.desvios_bollinger)
        keltner = ind.canal_keltner(
            quadro["maxima"], quadro["minima"], fechamento,
            p.periodo_keltner, p.periodo_atr, p.multiplo_keltner,
        )
        comprimido = (bollinger["inferior"] > keltner["inferior"]) & (
            bollinger["superior"] < keltner["superior"]
        )
        return pd.DataFrame(
            {
                "bb_inferior": bollinger["inferior"],
                "bb_superior": bollinger["superior"],
                "kc_inferior": keltner["inferior"],
                "kc_superior": keltner["superior"],
                "comprimido": comprimido.astype("int8"),
                "momento": fechamento
                - ind.media_movel_simples(fechamento, p.periodo_momento),
                "atr": ind.faixa_verdadeira_media(
                    quadro["maxima"], quadro["minima"], fechamento, p.periodo_atr
                ),
            }
        )

    def gerar_sinais(self, quadro: pd.DataFrame) -> pd.DataFrame:
        p = self.p
        fechamento = quadro["fechamento"]
        painel = self.painel_indicadores(quadro)

        pronto = painel["bb_inferior"].notna() & painel["kc_inferior"].notna() & painel["momento"].notna()
        comprimido = painel["comprimido"].astype(bool)

        # A entrada e na barra em que a compressao ACABA - nao enquanto ela dura.
        # Ficar comprado durante a compressao e pagar taxa esperando.
        soltou = pronto & comprimido.shift(1).fillna(False) & ~comprimido

        direcao = pd.Series(
            np.select(
                [soltou & (painel["momento"] > 0), soltou & (painel["momento"] < 0)],
                [COMPRA, VENDA],
                default=NEUTRO,
            ),
            index=quadro.index,
            dtype="int8",
        )

        sinais = quadro_sinais(quadro.index)
        sinais["direcao"] = direcao
        sinais["forca"] = np.where(
            direcao == NEUTRO,
            0.0,
            np.clip((painel["momento"].abs() / painel["atr"]).fillna(0) / 2, 0.1, 1.0),
        )

        stop = painel["atr"] * p.multiplo_stop
        alvo = painel["atr"] * p.multiplo_alvo
        comprando, vendendo = direcao == COMPRA, direcao == VENDA
        sinais.loc[comprando, "stop"] = (fechamento - stop)[comprando]
        sinais.loc[comprando, "alvo"] = (fechamento + alvo)[comprando]
        sinais.loc[vendendo, "stop"] = (fechamento + stop)[vendendo]
        sinais.loc[vendendo, "alvo"] = (fechamento - alvo)[vendendo]
        sinais.loc[direcao != NEUTRO, "motivo"] = "volatilidade comprimida se soltando"

        sinais = descartar_protecao_invalida(sinais, fechamento)
        return validar_sinais(sinais, quadro.index)
