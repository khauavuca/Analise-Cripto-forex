"""Confluencia: varias estrategias votando na mesma barra.

Equivale ao CompositeAlphaModel do LEAN. A ideia e simples - so age quando as
estrategias concordam -, e costuma elevar a taxa de acerto ao custo de gerar
bem menos sinais.

Duas armadilhas, marcadas onde acontecem:

- **Componentes precisam ser independentes.** RSI, Estocastico e Williams %R
  sao o mesmo oscilador com nomes diferentes; a media dos tres nao e
  confluencia, e um sinal so com confianca inflada. Use
  `matriz_correlacao_sinais()` antes de compor.
- **O limiar e um parametro livre.** Calibrar ele nos mesmos dados que voce vai
  reportar e overfitting. Ele pertence a otimizacao walk-forward.
"""
from __future__ import annotations

from typing import Literal

import numpy as np
import pandas as pd

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

Modo = Literal["ponderado", "maioria", "unanime"]


def _empilhar(partes: list[tuple[pd.DataFrame, float]], coluna: str) -> pd.DataFrame:
    """Junta a mesma coluna de varios componentes, uma coluna por componente."""
    return pd.concat(
        [sinais[coluna] for sinais, _ in partes], axis=1, keys=range(len(partes))
    )


def _em_modo_estado(estrategia: Estrategia) -> Estrategia:
    """Reconstroi o componente em modo "estado".

    Componentes em modo "evento" so opinam na vela exata em que a condicao
    virou, e duas estrategias raramente viram na mesma vela - compor dois
    "eventos" produz quase nenhum sinal, e o que sai parece filtro rigoroso
    quando na verdade e coincidencia de calendario.

    Em modo "estado" cada componente diz o que acha *em toda* barra, a votacao
    acontece de verdade, e a composta converte o resultado para evento uma vez
    so, no fim.
    """
    parametros = getattr(estrategia, "p", None)
    if parametros is not None and getattr(parametros, "modo", None) == "evento":
        return type(estrategia)(parametros.com(modo="estado"))
    return estrategia


class EstrategiaComposta(Estrategia):
    def __init__(
        self,
        componentes: list[Estrategia] | list[tuple[Estrategia, float]],
        modo: Modo = "ponderado",
        limiar: float = 0.5,
        agrupar_como_evento: bool = True,
    ) -> None:
        if not componentes:
            raise ValueError("a composicao precisa de pelo menos uma estrategia.")
        if modo not in ("ponderado", "maioria", "unanime"):
            raise ValueError("modo deve ser 'ponderado', 'maioria' ou 'unanime'.")

        self.componentes: list[tuple[Estrategia, float]] = [
            (_em_modo_estado(item[0]), item[1])
            if isinstance(item, tuple)
            else (_em_modo_estado(item), 1.0)
            for item in componentes
        ]
        self.modo = modo
        self.limiar = limiar
        self.agrupar_como_evento = agrupar_como_evento
        self.nome = f"composta[{modo}@{limiar:g}]({'+'.join(e.nome for e, _ in self.componentes)})"

    def barras_de_aquecimento(self) -> int:
        return max(estrategia.barras_de_aquecimento() for estrategia, _ in self.componentes)

    def gerar_sinais(self, quadro: pd.DataFrame) -> pd.DataFrame:
        partes = [
            (estrategia.gerar_sinais(quadro), peso)
            for estrategia, peso in self.componentes
        ]

        direcao = self._votar(partes, quadro.index)
        if self.agrupar_como_evento:
            direcao = apenas_transicoes(direcao)

        sinais = quadro_sinais(quadro.index)
        sinais["direcao"] = direcao
        sinais["forca"] = self._confianca(partes, direcao)
        sinais["stop"], sinais["alvo"] = self._protecao_mais_conservadora(partes, direcao)
        sinais.loc[direcao != NEUTRO, "motivo"] = "confluencia entre componentes"

        sinais = descartar_protecao_invalida(sinais, quadro["fechamento"])
        return validar_sinais(sinais, quadro.index)

    def _escore(self, partes: list[tuple[pd.DataFrame, float]], indice: pd.Index) -> pd.Series:
        """Media ponderada de direcao vezes confianca, entre -1 e 1."""
        peso_total = sum(peso for _, peso in partes)
        acumulado = pd.Series(0.0, index=indice)
        for sinais, peso in partes:
            acumulado += peso * sinais["direcao"] * sinais["forca"]
        return acumulado / peso_total

    def _votar(self, partes, indice: pd.Index) -> pd.Series:
        direcoes = _empilhar(partes, "direcao")

        if self.modo == "unanime":
            # Todos os componentes que opinaram precisam apontar para o mesmo
            # lado, e pelo menos um precisa ter opinado.
            opinou = (direcoes != NEUTRO).sum(axis=1)
            soma = direcoes.sum(axis=1)
            concorda = (soma.abs() == opinou) & (opinou == len(partes))
            return pd.Series(
                np.where(concorda, np.sign(soma), NEUTRO), index=indice, dtype="int8"
            )

        if self.modo == "maioria":
            soma = direcoes.sum(axis=1)
            return pd.Series(np.sign(soma), index=indice, dtype="int8")

        escore = self._escore(partes, indice)
        return pd.Series(
            np.where(escore.abs() >= self.limiar, np.sign(escore), NEUTRO),
            index=indice,
            dtype="int8",
        )

    def _confianca(self, partes, direcao: pd.Series) -> pd.Series:
        escore = self._escore(partes, direcao.index).abs().clip(0.0, 1.0)
        return escore.where(direcao != NEUTRO, 0.0)

    def _protecao_mais_conservadora(
        self, partes, direcao: pd.Series
    ) -> tuple[pd.Series, pd.Series]:
        """Stop mais proximo e alvo mais proximo entre quem concordou.

        Nunca a media: a media entre dois stops pode cair do lado errado da
        entrada e transformar uma protecao em um trade nascido ja estopado.
        """
        # As colunas precisam receber nomes distintos por componente. Sem isso o
        # concat produz varias colunas chamadas "stop" e varias chamadas
        # "direcao", e o `where` - que alinha por NOME de coluna - nao casa
        # nenhuma delas e devolve tudo NaN, em silencio.
        stops = _empilhar(partes, "stop")
        alvos = _empilhar(partes, "alvo")
        concordou = _empilhar(partes, "direcao").eq(direcao, axis=0)

        stops_validos = stops.where(concordou)
        alvos_validos = alvos.where(concordou)

        comprando = direcao == COMPRA
        stop = pd.Series(np.nan, index=direcao.index)
        alvo = pd.Series(np.nan, index=direcao.index)

        # Comprado: stop mais alto e alvo mais baixo sao os mais proximos.
        stop[comprando] = stops_validos.max(axis=1)[comprando]
        alvo[comprando] = alvos_validos.min(axis=1)[comprando]

        vendendo = direcao == VENDA
        stop[vendendo] = stops_validos.min(axis=1)[vendendo]
        alvo[vendendo] = alvos_validos.max(axis=1)[vendendo]

        return stop, alvo


def matriz_correlacao_sinais(
    estrategias: list[Estrategia], quadro: pd.DataFrame
) -> pd.DataFrame:
    """Correlacao entre as direcoes emitidas por cada estrategia.

    Acima de 0,8 em modulo, duas estrategias sao a mesma coisa: some-las nao
    aumenta a evidencia, so a confianca aparente.
    """
    colunas = {
        estrategia.nome: estrategia.gerar_sinais(quadro)["direcao"].astype(float)
        for estrategia in estrategias
    }
    return pd.DataFrame(colunas).corr()
