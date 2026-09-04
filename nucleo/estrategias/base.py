"""Contrato de estrategia.

Equivale ao IAlphaModel do LEAN, que recebe dados e devolve Insights: aqui a
estrategia recebe um quadro de velas e devolve, para cada barra, uma direcao,
uma confianca e os niveis de protecao.

Duas regras estruturais:

1. **O quadro de saida tem exatamente o mesmo indice do de entrada.** Nada de
   `dropna` ou reindex - diferenca de comprimento e uma das formas mais comuns
   de look-ahead entrar sem ninguem ver.
2. **A estrategia nao desloca nada.** A linha `i` e decidida com informacao ate
   o fechamento da barra `i`, inclusive. Quem aplica o atraso de execucao e o
   motor de backtest, num unico lugar - assim uma estrategia nova nao tem como
   esquecer de fazer isso.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np
import pandas as pd

COLUNAS_SINAL = ["direcao", "forca", "stop", "alvo", "motivo"]

COMPRA = 1
VENDA = -1
NEUTRO = 0


def quadro_sinais(indice: pd.Index) -> pd.DataFrame:
    """Quadro de sinais zerado, no formato canonico."""
    return pd.DataFrame(
        {
            "direcao": np.zeros(len(indice), dtype="int8"),
            "forca": np.zeros(len(indice), dtype="float64"),
            "stop": np.full(len(indice), np.nan),
            "alvo": np.full(len(indice), np.nan),
            "motivo": pd.Series([""] * len(indice), index=indice, dtype="object"),
        },
        index=indice,
    )


def apenas_transicoes(direcao: pd.Series) -> pd.Series:
    """Mantem so a barra em que a condicao passou a valer.

    Uma condicao como `rsi < 40 e macd > sinal` fica verdadeira por uma
    *sequencia* de barras, nao so na que cruzou. Consultada a cada 30 segundos
    como escalar isso parecia um sinal; como coluna vetorial viram 5 a 15
    entradas consecutivas. Tratar estado como evento infla a contagem de
    trades em varias vezes, leva junto a conta de taxas, e destroi qualquer
    metrica por trade.
    """
    anterior = direcao.shift(1).fillna(NEUTRO)
    return direcao.where(direcao != anterior, NEUTRO).astype("int8")


def validar_sinais(sinais: pd.DataFrame, indice: pd.Index) -> pd.DataFrame:
    """Garante o contrato antes de o motor consumir."""
    if not sinais.index.equals(indice):
        raise ValueError(
            "A estrategia devolveu um indice diferente do quadro de entrada. "
            "Nunca use dropna() nem reindex ao montar os sinais."
        )
    faltando = [coluna for coluna in COLUNAS_SINAL if coluna not in sinais.columns]
    if faltando:
        raise ValueError(f"Sinais sem as colunas obrigatorias: {faltando}")
    if not sinais["direcao"].isin([COMPRA, VENDA, NEUTRO]).all():
        raise ValueError("A coluna direcao so aceita -1, 0 ou 1.")
    return sinais


class Estrategia(ABC):
    """Transforma velas em sinais. Implemente `gerar_sinais`."""

    nome: str = "abstrata"

    def barras_de_aquecimento(self) -> int:
        """Quantas barras a estrategia precisa antes do primeiro sinal valido.

        O carregador usa isso para buscar historico extra. Foi a ausencia
        disso que deixou o codigo antigo pedindo 100 velas para calcular uma
        media de 200 - a media longa ficava NaN, o filtro de tendencia nunca
        filtrava nada e ninguem percebeu.
        """
        return 200

    def painel_indicadores(self, quadro: pd.DataFrame) -> pd.DataFrame:
        """Tudo que a estrategia enxerga, barra a barra.

        Serve ao acompanhamento ao vivo: sem isso, uma barra sem sinal e uma
        caixa preta - nao da para saber se o gatilho passou perto ou nem chegou
        perto. Devolve quadro vazio por padrao.
        """
        return pd.DataFrame(index=quadro.index)

    @abstractmethod
    def gerar_sinais(self, quadro: pd.DataFrame) -> pd.DataFrame:
        """Colunas: direcao (-1/0/1), forca (0..1), stop, alvo, motivo."""

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.nome}>"


def descartar_protecao_invalida(sinais: pd.DataFrame, fechamento: pd.Series) -> pd.DataFrame:
    """Anula sinais cujo stop ou alvo esta do lado errado do preco.

    Acontece de verdade quando o canal e o ATR se cruzam num periodo de baixa
    volatilidade. Sem essa checagem o trade nasce ja estopado e polui a
    estatistica com uma perda que nunca teria sido tomada na pratica.
    """
    compra = sinais["direcao"] == COMPRA
    venda = sinais["direcao"] == VENDA

    ruim = (
        (compra & ((sinais["stop"] >= fechamento) | (sinais["alvo"] <= fechamento)))
        | (venda & ((sinais["stop"] <= fechamento) | (sinais["alvo"] >= fechamento)))
        | (sinais["direcao"] != NEUTRO) & (sinais["stop"].isna() | sinais["alvo"].isna())
    )

    sinais.loc[ruim, ["direcao", "forca"]] = [NEUTRO, 0.0]
    sinais.loc[ruim, ["stop", "alvo"]] = np.nan
    sinais.loc[ruim, "motivo"] = "protecao invalida"
    return sinais
