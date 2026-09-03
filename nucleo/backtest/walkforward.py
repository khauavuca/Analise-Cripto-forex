"""Validacao walk-forward: otimiza no passado, mede no que veio depois.

Otimizar parametros no historico inteiro e reportar aquele resultado e a forma
mais eficiente de produzir um numero excelente e falso. Aqui a grade e testada
so na janela de treino, e o numero que vale e o das janelas de teste, que a
otimizacao nunca viu.

Espere que o resultado fora da amostra seja bem pior que o de dentro. Essa
diferenca *e* a informacao: ela mostra quanto da estrategia era sinal e quanto
era decoreba de ruido.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..estrategias.base import Estrategia
from .metricas import Metricas, calcular
from .motor import ConfigExecucao, ModeloCustos, executar


@dataclass(frozen=True)
class Janela:
    treino_inicio: pd.Timestamp
    treino_fim: pd.Timestamp
    teste_inicio: pd.Timestamp
    teste_fim: pd.Timestamp

    def __str__(self) -> str:
        return (
            f"treino {self.treino_inicio:%Y-%m-%d}..{self.treino_fim:%Y-%m-%d} | "
            f"teste {self.teste_inicio:%Y-%m-%d}..{self.teste_fim:%Y-%m-%d}"
        )


@dataclass
class RelatorioWalkForward:
    janelas: list[Janela]
    escolhas: list[dict]
    trades_fora_da_amostra: pd.DataFrame
    curva_fora_da_amostra: pd.Series
    metricas: Metricas | None
    metricas_dentro_da_amostra: list[Metricas] = field(default_factory=list)
    configuracoes_testadas: int = 0

    def resumo(self) -> str:
        linhas = [
            f"Janelas: {len(self.janelas)} | configuracoes testadas por janela: "
            f"{self.configuracoes_testadas}",
        ]
        if self.configuracoes_testadas > 1:
            linhas.append(
                f"  Atencao: testar {self.configuracoes_testadas} combinacoes e escolher "
                "a melhor ja e uma forma de\n  otimismo. Prefira platos a picos: se 14 "
                "funciona e 13 e 15 nao, 14 e ruido."
            )
        for janela, escolha in zip(self.janelas, self.escolhas):
            linhas.append(f"  {janela} -> {escolha}")
        return "\n".join(linhas)


def gerar_janelas(
    indice: pd.DatetimeIndex,
    meses_treino: int = 12,
    meses_teste: int = 3,
    modo: str = "deslizante",
) -> list[Janela]:
    if modo not in ("deslizante", "ancorado"):
        raise ValueError("modo deve ser 'deslizante' ou 'ancorado'.")
    if len(indice) == 0:
        return []

    inicio_dados, fim_dados = indice[0], indice[-1]
    janelas: list[Janela] = []
    treino_inicio = inicio_dados

    while True:
        treino_fim = treino_inicio + pd.DateOffset(months=meses_treino)
        teste_fim = treino_fim + pd.DateOffset(months=meses_teste)
        if treino_fim >= fim_dados:
            break

        janelas.append(
            Janela(
                treino_inicio=treino_inicio if modo == "deslizante" else inicio_dados,
                treino_fim=treino_fim,
                teste_inicio=treino_fim,
                teste_fim=min(teste_fim, fim_dados),
            )
        )
        if teste_fim >= fim_dados:
            break
        treino_inicio = treino_inicio + pd.DateOffset(months=meses_teste)

    return janelas


def executar_walkforward(
    quadro: pd.DataFrame,
    fabrica: Callable[[dict], Estrategia],
    grade: Sequence[dict],
    timeframe: str,
    custos: ModeloCustos | None = None,
    config: ConfigExecucao | None = None,
    meses_treino: int = 12,
    meses_teste: int = 3,
    modo: str = "deslizante",
    criterio: str = "expectancia_r",
) -> RelatorioWalkForward:
    custos = custos or ModeloCustos()
    config = config or ConfigExecucao()
    grade = list(grade) or [{}]

    janelas = gerar_janelas(quadro.index, meses_treino, meses_teste, modo)
    if not janelas:
        raise ValueError(
            f"Historico curto demais: {len(quadro)} barras nao cobrem uma janela de "
            f"{meses_treino} meses de treino mais {meses_teste} de teste."
        )

    # Os sinais de cada combinacao sao calculados uma vez sobre o quadro
    # inteiro. Isso e seguro porque todo indicador aqui e causal - o valor da
    # barra i nao muda quando chegam barras depois dela, e ha teste provando -,
    # e assim cada janela ja nasce com o aquecimento resolvido.
    sinais_por_configuracao = [
        (parametros, fabrica(parametros).gerar_sinais(quadro)) for parametros in grade
    ]

    escolhas: list[dict] = []
    metricas_treino: list[Metricas] = []
    trades_teste: list[pd.DataFrame] = []
    curvas_teste: list[pd.Series] = []

    for janela in janelas:
        fatia_treino = quadro.loc[janela.treino_inicio : janela.treino_fim]
        fatia_teste = quadro.loc[janela.teste_inicio : janela.teste_fim]
        if len(fatia_treino) < 10 or len(fatia_teste) < 10:
            continue

        escolhida, melhor_nota, melhor_metrica = None, -np.inf, None
        for posicao, (parametros, sinais) in enumerate(sinais_por_configuracao):
            resultado = executar(
                fatia_treino, sinais.loc[fatia_treino.index], custos, config
            )
            metrica = calcular(
                resultado.trades, resultado.curva_capital, fatia_treino, timeframe
            )
            nota = getattr(metrica, criterio)
            if metrica.n_trades > 0 and np.isfinite(nota) and nota > melhor_nota:
                escolhida, melhor_nota, melhor_metrica = posicao, nota, metrica

        if escolhida is None:
            # Nenhuma combinacao produziu trade no treino desta janela: nao ha
            # base para escolher, entao a janela e pulada em vez de chutar.
            escolhas.append({"parametros": None, "nota_treino": None})
            continue

        melhor_parametros, sinais_escolhidos = sinais_por_configuracao[escolhida]
        escolhas.append(
            {"parametros": melhor_parametros, f"{criterio}_treino": round(melhor_nota, 3)}
        )
        metricas_treino.append(melhor_metrica)

        resultado_teste = executar(
            fatia_teste, sinais_escolhidos.loc[fatia_teste.index], custos, config
        )
        trades_teste.append(resultado_teste.trades)
        curvas_teste.append(resultado_teste.curva_capital)

    trades = (
        pd.concat(trades_teste, ignore_index=True)
        if trades_teste
        else pd.DataFrame(columns=["motivo_saida"])
    )
    curva = _emendar_curvas(curvas_teste)
    metricas = (
        calcular(trades, curva, quadro.loc[janelas[0].teste_inicio :], timeframe)
        if len(curva)
        else None
    )

    return RelatorioWalkForward(
        janelas=janelas,
        escolhas=escolhas,
        trades_fora_da_amostra=trades,
        curva_fora_da_amostra=curva,
        metricas=metricas,
        metricas_dentro_da_amostra=metricas_treino,
        configuracoes_testadas=len(grade),
    )


def _emendar_curvas(curvas: list[pd.Series]) -> pd.Series:
    """Encadeia as curvas das janelas de teste, compondo os retornos."""
    if not curvas:
        return pd.Series(dtype=float)

    partes: list[pd.Series] = []
    acumulado = 1.0
    for curva in curvas:
        if curva.empty:
            continue
        normalizada = curva / curva.iloc[0] * acumulado
        partes.append(normalizada)
        acumulado = float(normalizada.iloc[-1])

    if not partes:
        return pd.Series(dtype=float)

    emendada = pd.concat(partes)
    return emendada[~emendada.index.duplicated(keep="last")].sort_index()


def grade_de_parametros(**opcoes: Sequence) -> list[dict]:
    """Produto cartesiano das opcoes: grade_de_parametros(rsi_compra=[30, 40])."""
    from itertools import product

    if not opcoes:
        return [{}]
    nomes = list(opcoes)
    return [
        dict(zip(nomes, valores)) for valores in product(*(opcoes[nome] for nome in nomes))
    ]
