"""RSI + MACD com filtro de tendencia - a linha de base do projeto.

E a estrategia que ja existia em `Market_Analyzer.varredura_dupla()`, portada
para o contrato vetorial. A logica de entrada foi preservada de proposito: ela
existe para ser *medida*, e so faz sentido comparar melhorias contra ela.

O que mudou, e por que:

- **RSI de Wilder** no lugar da media simples. Medido em 4.319 velas de
  BTC/USDT 1h, os dois discordam sobre cruzar 40 em 13,4% das barras.
- **A media de 200 agora existe.** Antes o analisador pedia 100 velas e
  calculava `rolling(200)`, entao a media longa era sempre NaN, a tendencia
  era sempre LATERAL e os filtros `!= BAIXA` / `!= ALTA` nunca filtravam nada.
- **Suporte e resistencia deslocados** uma barra, para nao usar a maxima da
  propria vela que se esta analisando como alvo dela.
- **Modo evento por padrao**, para nao emitir a mesma entrada em barras
  seguidas enquanto a condicao permanece verdadeira.
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
class ParametrosRsiMacd:
    periodo_rsi: int = 14
    rsi_compra: float = 40.0
    rsi_venda: float = 60.0
    macd_rapida: int = 12
    macd_lenta: int = 26
    macd_sinal: int = 9
    sma_curta: int = 20
    sma_media: int = 50
    sma_longa: int = 200
    periodo_atr: int = 14
    periodo_canal: int = 50
    folga_atr: float = 0.5
    modo: str = "evento"  # "evento" dispara na virada; "estado" enquanto valer

    def com(self, **mudancas) -> "ParametrosRsiMacd":
        return replace(self, **mudancas)


class EstrategiaRsiMacd(Estrategia):
    def __init__(self, parametros: ParametrosRsiMacd | None = None) -> None:
        self.p = parametros or ParametrosRsiMacd()
        if self.p.modo not in ("evento", "estado"):
            raise ValueError("modo deve ser 'evento' ou 'estado'.")
        self.nome = f"rsi_macd({self.p.rsi_compra:g}/{self.p.rsi_venda:g},{self.p.modo})"

    def barras_de_aquecimento(self) -> int:
        # A suavizacao de Wilder e uma EMA: nao "termina", so converge. Umas 5
        # constantes de tempo deixam o residuo abaixo de 1%, dai o periodo * 5.
        return (
            max(
                self.p.sma_longa,
                self.p.periodo_canal,
                self.p.macd_lenta + self.p.macd_sinal,
                self.p.periodo_rsi * 5,
                self.p.periodo_atr * 5,
            )
            + 20
        )

    def gerar_sinais(self, quadro: pd.DataFrame) -> pd.DataFrame:
        p = self.p
        fechamento = quadro["fechamento"]

        rsi = ind.indice_forca_relativa(fechamento, p.periodo_rsi)
        linhas = ind.macd(fechamento, p.macd_rapida, p.macd_lenta, p.macd_sinal)
        curta = ind.media_movel_simples(fechamento, p.sma_curta)
        media = ind.media_movel_simples(fechamento, p.sma_media)
        longa = ind.media_movel_simples(fechamento, p.sma_longa)
        canal = ind.canal_donchian(quadro["maxima"], quadro["minima"], p.periodo_canal)
        atr = ind.faixa_verdadeira_media(
            quadro["maxima"], quadro["minima"], fechamento, p.periodo_atr
        )

        # Tendencia pelo alinhamento das medias: +1 alta, -1 baixa, 0 lateral.
        tendencia = pd.Series(NEUTRO, index=quadro.index, dtype="int8")
        tendencia[(curta > media) & (media > longa)] = COMPRA
        tendencia[(curta < media) & (media < longa)] = VENDA

        # Comparacao com NaN da False, entao as barras de aquecimento saem
        # naturalmente como NEUTRO - sem precisar recortar o quadro.
        quer_comprar = (
            (rsi < p.rsi_compra) & (linhas["macd"] > linhas["sinal"]) & (tendencia != VENDA)
        )
        quer_vender = (
            (rsi > p.rsi_venda) & (linhas["macd"] < linhas["sinal"]) & (tendencia != COMPRA)
        )

        direcao = pd.Series(
            np.select([quer_comprar, quer_vender], [COMPRA, VENDA], default=NEUTRO),
            index=quadro.index,
            dtype="int8",
        )
        if p.modo == "evento":
            direcao = apenas_transicoes(direcao)

        sinais = quadro_sinais(quadro.index)
        sinais["direcao"] = direcao
        sinais["forca"] = self._forca(rsi, tendencia, direcao)

        folga = atr * p.folga_atr
        comprando = direcao == COMPRA
        vendendo = direcao == VENDA
        sinais.loc[comprando, "stop"] = (canal["suporte"] - folga)[comprando]
        sinais.loc[comprando, "alvo"] = canal["resistencia"][comprando]
        sinais.loc[vendendo, "stop"] = (canal["resistencia"] + folga)[vendendo]
        sinais.loc[vendendo, "alvo"] = canal["suporte"][vendendo]

        sinais.loc[comprando, "motivo"] = "rsi baixo + macd virando para cima"
        sinais.loc[vendendo, "motivo"] = "rsi alto + macd virando para baixo"

        sinais = descartar_protecao_invalida(sinais, fechamento)
        return validar_sinais(sinais, quadro.index)

    def _forca(
        self, rsi: pd.Series, tendencia: pd.Series, direcao: pd.Series
    ) -> pd.Series:
        """Confianca de 0 a 1 - heuristica, nao calibrada.

        Serve para ordenar sinais e alimentar a confluencia da estrategia
        composta. Nao trate como probabilidade: quem estima probabilidade e o
        backtest, olhando resultado realizado.
        """
        extremo = np.where(
            direcao == COMPRA,
            (self.p.rsi_compra - rsi) / self.p.rsi_compra,
            np.where(direcao == VENDA, (rsi - self.p.rsi_venda) / (100 - self.p.rsi_venda), 0.0),
        )
        base = 0.5 + 0.35 * np.clip(extremo, 0.0, 1.0)
        alinhado = (direcao != NEUTRO) & (direcao == tendencia)
        forca = np.where(alinhado, base + 0.15, base)
        return pd.Series(
            np.where(direcao == NEUTRO, 0.0, np.clip(forca, 0.0, 1.0)),
            index=rsi.index,
            dtype="float64",
        )
