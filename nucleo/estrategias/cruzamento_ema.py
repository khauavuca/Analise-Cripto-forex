"""Cruzamento de medias exponenciais com filtro de forca de tendencia.

Existe por dois motivos. Primeiro, para ter um segundo ponto de comparacao: uma
estrategia sozinha nao diz se o resultado veio dela ou do periodo. Segundo,
porque ela e *estruturalmente diferente* da RSI+MACD - segue tendencia em vez
de apostar na reversao -, o que a torna util na confluencia. Compor tres
osciladores parecidos nao e confluencia, e o mesmo sinal com tres votos.

Stop e alvo saem do ATR, nao de suporte e resistencia: em tendencia os niveis
do canal ficam longe demais e o alvo nunca e alcancado.
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
class ParametrosCruzamentoEma:
    ema_rapida: int = 21
    ema_lenta: int = 55
    periodo_atr: int = 14
    periodo_adx: int = 14
    adx_minimo: float = 20.0
    multiplo_stop: float = 2.0
    multiplo_alvo: float = 3.0
    modo: str = "evento"

    def com(self, **mudancas) -> "ParametrosCruzamentoEma":
        return replace(self, **mudancas)


class EstrategiaCruzamentoEma(Estrategia):
    def __init__(self, parametros: ParametrosCruzamentoEma | None = None) -> None:
        self.p = parametros or ParametrosCruzamentoEma()
        if self.p.ema_rapida >= self.p.ema_lenta:
            raise ValueError("a media rapida precisa ter periodo menor que a lenta.")
        self.nome = f"ema({self.p.ema_rapida}/{self.p.ema_lenta},{self.p.modo})"

    def barras_de_aquecimento(self) -> int:
        return max(self.p.ema_lenta * 3, self.p.periodo_adx * 5, self.p.periodo_atr * 5) + 20

    def painel_indicadores(self, quadro: pd.DataFrame) -> pd.DataFrame:
        p = self.p
        fechamento = quadro["fechamento"]
        direcional = ind.indice_direcional_medio(
            quadro["maxima"], quadro["minima"], fechamento, p.periodo_adx
        )
        return pd.DataFrame(
            {
                "ema_rapida": ind.media_movel_exponencial(fechamento, p.ema_rapida),
                "ema_lenta": ind.media_movel_exponencial(fechamento, p.ema_lenta),
                "adx": direcional["adx"],
                "di_mais": direcional["di_mais"],
                "di_menos": direcional["di_menos"],
                "atr": ind.faixa_verdadeira_media(
                    quadro["maxima"], quadro["minima"], fechamento, p.periodo_atr
                ),
            }
        )

    def gerar_sinais(self, quadro: pd.DataFrame) -> pd.DataFrame:
        p = self.p
        fechamento = quadro["fechamento"]
        painel = self.painel_indicadores(quadro)

        rapida = painel["ema_rapida"]
        lenta = painel["ema_lenta"]
        atr = painel["atr"]
        adx = painel["adx"]

        # ADX mede forca de tendencia sem dizer a direcao. Cruzamento de medias
        # em mercado lateral e uma maquina de gerar sinal falso e pagar taxa.
        tem_tendencia = adx >= p.adx_minimo

        # Nenhum sinal antes de todos os indicadores estarem prontos - inclusive
        # o ATR, que so aparece no stop mas cuja ausencia invalidaria o trade.
        pronto = rapida.notna() & lenta.notna() & adx.notna() & atr.notna()

        direcao = pd.Series(
            np.select(
                [
                    pronto & (rapida > lenta) & tem_tendencia,
                    pronto & (rapida < lenta) & tem_tendencia,
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

        # Confianca cresce com a forca da tendencia, saturando em ADX 50.
        sinais["forca"] = np.where(
            direcao == NEUTRO, 0.0, np.clip(adx.fillna(0) / 50.0, 0.0, 1.0)
        )

        comprando = direcao == COMPRA
        vendendo = direcao == VENDA
        sinais.loc[comprando, "stop"] = (fechamento - atr * p.multiplo_stop)[comprando]
        sinais.loc[comprando, "alvo"] = (fechamento + atr * p.multiplo_alvo)[comprando]
        sinais.loc[vendendo, "stop"] = (fechamento + atr * p.multiplo_stop)[vendendo]
        sinais.loc[vendendo, "alvo"] = (fechamento - atr * p.multiplo_alvo)[vendendo]

        sinais.loc[comprando, "motivo"] = "media rapida cruzou para cima com tendencia"
        sinais.loc[vendendo, "motivo"] = "media rapida cruzou para baixo com tendencia"

        sinais = descartar_protecao_invalida(sinais, fechamento)
        return validar_sinais(sinais, quadro.index)
