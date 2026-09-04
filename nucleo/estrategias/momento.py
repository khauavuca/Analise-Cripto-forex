"""Momento temporal - a estrategia mais documentada da literatura academica.

Nao usa indicador nenhum para decidir a direcao: olha o retorno acumulado dos
ultimos N periodos e opera a favor do sinal dele. E o `time series momentum` de
Moskowitz, Ooi e Pedersen (2012), medido em decadas e em dezenas de mercados, e
o motor declarado de boa parte da industria de managed futures.

O que a torna diferente das outras aqui: ela nao tenta acertar reversao nem
rompimento. Ela assume que o que subiu tende a continuar subindo por um tempo, e
o unico julgamento e sobre a janela.

O filtro por volatilidade tambem e do original: um retorno de 5% num ativo
parado significa muito mais que 5% num ativo que oscila 5% por dia. Comparar
retorno bruto entre ativos com volatilidade diferente e comparar coisas
distintas.
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
class ParametrosMomento:
    periodo_momento: int = 60
    periodo_volatilidade: int = 60
    limiar_normalizado: float = 0.5
    periodo_atr: int = 20
    multiplo_stop: float = 3.0
    multiplo_alvo: float = 6.0
    modo: str = "evento"

    def com(self, **mudancas) -> "ParametrosMomento":
        return replace(self, **mudancas)


class EstrategiaMomentoTemporal(Estrategia):
    def __init__(self, parametros: ParametrosMomento | None = None) -> None:
        self.p = parametros or ParametrosMomento()
        self.nome = f"momento({self.p.periodo_momento},{self.p.limiar_normalizado:g}sd)"

    def barras_de_aquecimento(self) -> int:
        return (
            max(
                self.p.periodo_momento + self.p.periodo_volatilidade,
                self.p.periodo_atr * 5,
            )
            + 30
        )

    def painel_indicadores(self, quadro: pd.DataFrame) -> pd.DataFrame:
        p = self.p
        fechamento = quadro["fechamento"]

        retorno = ind.retorno_periodo(fechamento, p.periodo_momento)
        volatilidade = ind.volatilidade_realizada(fechamento, p.periodo_volatilidade)

        # Retorno em unidades de desvio da propria janela: e assim que se
        # compara momento entre ativos de volatilidade diferente.
        with np.errstate(divide="ignore", invalid="ignore"):
            normalizado = retorno / (volatilidade * np.sqrt(p.periodo_momento))

        return pd.DataFrame(
            {
                "retorno": retorno,
                "volatilidade": volatilidade,
                "momento_normalizado": normalizado.replace([np.inf, -np.inf], np.nan),
                "atr": ind.faixa_verdadeira_media(
                    quadro["maxima"], quadro["minima"], fechamento, p.periodo_atr
                ),
            }
        )

    def gerar_sinais(self, quadro: pd.DataFrame) -> pd.DataFrame:
        p = self.p
        fechamento = quadro["fechamento"]
        painel = self.painel_indicadores(quadro)

        pronto = painel["momento_normalizado"].notna() & painel["atr"].notna()
        forte = painel["momento_normalizado"].abs() >= p.limiar_normalizado

        direcao = pd.Series(
            np.select(
                [
                    pronto & forte & (painel["momento_normalizado"] > 0),
                    pronto & forte & (painel["momento_normalizado"] < 0),
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
        sinais["forca"] = np.where(
            direcao == NEUTRO,
            0.0,
            np.clip(painel["momento_normalizado"].abs().fillna(0) / 2.0, 0.1, 1.0),
        )

        # Stop e alvo largos de proposito: seguir tendencia so paga se a posicao
        # aguentar o repuxo normal do caminho.
        stop = painel["atr"] * p.multiplo_stop
        alvo = painel["atr"] * p.multiplo_alvo
        comprando, vendendo = direcao == COMPRA, direcao == VENDA
        sinais.loc[comprando, "stop"] = (fechamento - stop)[comprando]
        sinais.loc[comprando, "alvo"] = (fechamento + alvo)[comprando]
        sinais.loc[vendendo, "stop"] = (fechamento + stop)[vendendo]
        sinais.loc[vendendo, "alvo"] = (fechamento - alvo)[vendendo]
        sinais.loc[direcao != NEUTRO, "motivo"] = "momento acumulado acima do ruido"

        sinais = descartar_protecao_invalida(sinais, fechamento)
        return validar_sinais(sinais, quadro.index)
