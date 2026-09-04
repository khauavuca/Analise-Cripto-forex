"""Estrutura de mercado - a leitura que o operador discricionario faz na mao.

Nao usa indicador nenhum: so topos e fundos. A tese e classica de price action -
enquanto o mercado faz topos e fundos ascendentes, a estrutura e de alta, e o
rompimento do ultimo topo confirmado e continuacao. O stop vai abaixo do ultimo
fundo, porque e ele que, se perdido, invalida a leitura. O alvo e a projecao da
perna anterior.

**A armadilha que este setup carrega, e por que ela e tratada em
`indicadores.pivos`:** um topo so vira topo depois que N velas mais baixas
aparecem. Marcar o topo na vela em que ele ocorreu e operar ali significa saber
o que so seria conhecido N velas depois. Backtest de estrutura feito assim da
resultado espetacular e completamente falso. Aqui todo pivo entra no sistema
com o atraso de confirmacao dele.
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
    descartar_protecao_invalida,
    quadro_sinais,
    validar_sinais,
)


@dataclass(frozen=True)
class ParametrosEstrutura:
    # 5 barras de cada lado e o que um operador chamaria de topo visivel no
    # grafico. Com 3, quase toda oscilacao vira pivo e o "rompimento de
    # estrutura" acontece a cada poucas velas - o que nao e leitura de
    # estrutura, e ruido com nome bonito.
    esquerda: int = 5
    direita: int = 5
    periodo_atr: int = 14
    folga_stop_atr: float = 0.5
    projecao_alvo: float = 1.0
    exigir_estrutura: bool = True

    def com(self, **mudancas) -> "ParametrosEstrutura":
        return replace(self, **mudancas)


class EstrategiaEstruturaMercado(Estrategia):
    """Compra o rompimento do ultimo topo confirmado, com estrutura de alta."""

    def __init__(self, parametros: ParametrosEstrutura | None = None) -> None:
        self.p = parametros or ParametrosEstrutura()
        self.nome = f"estrutura({self.p.esquerda}/{self.p.direita})"

    def barras_de_aquecimento(self) -> int:
        return (self.p.esquerda + self.p.direita + 1) * 20 + self.p.periodo_atr * 5 + 30

    def painel_indicadores(self, quadro: pd.DataFrame) -> pd.DataFrame:
        p = self.p
        pivo = ind.pivos(quadro["maxima"], quadro["minima"], p.esquerda, p.direita)

        # Valor do pivo ANTERIOR, para comparar topo com topo e fundo com fundo.
        # Precisa ser o penultimo pivo confirmado, nao a barra anterior - por
        # isso a serie e reduzida so aos instantes de confirmacao antes do shift.
        anterior_topo = (
            pivo["topo"].where(pivo["topo_novo"]).dropna().shift(1)
            .reindex(quadro.index).ffill()
        )
        anterior_fundo = (
            pivo["fundo"].where(pivo["fundo_novo"]).dropna().shift(1)
            .reindex(quadro.index).ffill()
        )

        return pd.DataFrame(
            {
                "topo": pivo["topo"],
                "fundo": pivo["fundo"],
                "topo_anterior": anterior_topo,
                "fundo_anterior": anterior_fundo,
                "atr": ind.faixa_verdadeira_media(
                    quadro["maxima"], quadro["minima"], quadro["fechamento"], p.periodo_atr
                ),
            }
        )

    def gerar_sinais(self, quadro: pd.DataFrame) -> pd.DataFrame:
        p = self.p
        fechamento = quadro["fechamento"]
        painel = self.painel_indicadores(quadro)

        pronto = (
            painel["topo"].notna()
            & painel["fundo"].notna()
            & painel["topo_anterior"].notna()
            & painel["fundo_anterior"].notna()
            & painel["atr"].notna()
        )

        alta = (painel["topo"] > painel["topo_anterior"]) & (
            painel["fundo"] > painel["fundo_anterior"]
        )
        baixa = (painel["topo"] < painel["topo_anterior"]) & (
            painel["fundo"] < painel["fundo_anterior"]
        )
        if not p.exigir_estrutura:
            alta = baixa = pd.Series(True, index=quadro.index)

        acima = fechamento > painel["topo"]
        abaixo = fechamento < painel["fundo"]

        # O sinal e o instante do rompimento, nao o periodo em que o preco fica
        # acima do topo. Sem isso a mesma leitura viraria dezenas de entradas.
        rompeu_topo = acima & ~acima.shift(1).fillna(False)
        rompeu_fundo = abaixo & ~abaixo.shift(1).fillna(False)

        direcao = pd.Series(
            np.select(
                [pronto & alta & rompeu_topo, pronto & baixa & rompeu_fundo],
                [COMPRA, VENDA],
                default=NEUTRO,
            ),
            index=quadro.index,
            dtype="int8",
        )

        sinais = quadro_sinais(quadro.index)
        sinais["direcao"] = direcao

        # A perna e a distancia entre o ultimo fundo e o ultimo topo: estrutura
        # larga significa movimento com convicao.
        perna = (painel["topo"] - painel["fundo"]).abs()
        sinais["forca"] = np.where(
            direcao == NEUTRO, 0.0, np.clip((perna / painel["atr"]).fillna(0) / 8.0, 0.1, 1.0)
        )

        folga = painel["atr"] * p.folga_stop_atr
        comprando, vendendo = direcao == COMPRA, direcao == VENDA

        # Comprado: o stop vai abaixo do fundo que sustenta a estrutura. Se ele
        # cair, a leitura de alta deixou de valer - e esse o ponto de saida
        # logico, nao uma distancia arbitraria.
        sinais.loc[comprando, "stop"] = (painel["fundo"] - folga)[comprando]
        sinais.loc[comprando, "alvo"] = (fechamento + perna * p.projecao_alvo)[comprando]
        sinais.loc[vendendo, "stop"] = (painel["topo"] + folga)[vendendo]
        sinais.loc[vendendo, "alvo"] = (fechamento - perna * p.projecao_alvo)[vendendo]
        sinais.loc[direcao != NEUTRO, "motivo"] = "rompimento de estrutura confirmada"

        sinais = descartar_protecao_invalida(sinais, fechamento)
        return validar_sinais(sinais, quadro.index)
