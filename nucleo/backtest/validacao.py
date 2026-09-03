"""Checagens que separam vantagem real de sorte e de bug.

Um backtest positivo nao prova nada sozinho. Estas tres checagens sao baratas
e derrubam a maior parte das estrategias que "funcionam":

- **Permutacao**: se sinais aleatorios batem o seu com frequencia, voce nao tem
  vantagem, tem um resultado dentro do ruido.
- **Sensibilidade a atraso**: vantagem real degrada devagar quando voce demora
  mais para executar. Artefato de look-ahead **colapsa**. E um detector
  automatico de vazamento.
- **Sensibilidade a custo**: vantagem que morre ao dobrar a taxa estava dentro
  da banda de erro o tempo todo.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .motor import ConfigExecucao, ModeloCustos, executar


@dataclass
class ResultadoPermutacao:
    retorno_real: float
    retorno_medio_aleatorio: float
    percentil_do_real: float
    p_valor: float
    repeticoes: int

    @property
    def tem_sinal(self) -> bool:
        return self.p_valor <= 0.05

    def resumo(self) -> str:
        veredicto = (
            "o resultado esta fora do que o acaso produz"
            if self.tem_sinal
            else "INDISTINGUIVEL DE ACASO - nao ha evidencia de vantagem"
        )
        return (
            f"Permutacao ({self.repeticoes} embaralhamentos): "
            f"retorno real {self.retorno_real:+.1%} contra media aleatoria "
            f"{self.retorno_medio_aleatorio:+.1%} | p = {self.p_valor:.3f} -> {veredicto}"
        )


def _retorno(quadro, sinais, custos, config) -> float:
    resultado = executar(quadro, sinais, custos, config)
    curva = resultado.curva_capital
    return float(curva.iloc[-1] / curva.iloc[0] - 1) if len(curva) else 0.0


def _rotacionar(sinais: pd.DataFrame, deslocamento: int) -> pd.DataFrame:
    """Gira os sinais no tempo, preservando a estrutura deles.

    Rotacao e nao embaralhamento simples: assim o numero de sinais e o
    agrupamento entre eles continuam identicos, e a unica coisa destruida e o
    alinhamento com o preco - que e exatamente a hipotese sob teste.
    """
    girado = sinais.copy()
    for coluna in sinais.columns:
        girado[coluna] = np.roll(sinais[coluna].to_numpy(), deslocamento)
    return girado


def teste_permutacao(
    quadro: pd.DataFrame,
    sinais: pd.DataFrame,
    custos: ModeloCustos | None = None,
    config: ConfigExecucao | None = None,
    repeticoes: int = 300,
    semente: int = 42,
) -> ResultadoPermutacao:
    custos = custos or ModeloCustos()
    config = config or ConfigExecucao()

    real = _retorno(quadro, sinais, custos, config)
    gerador = np.random.default_rng(semente)
    total = len(sinais)

    amostras = np.empty(repeticoes)
    for i in range(repeticoes):
        deslocamento = int(gerador.integers(1, total))
        amostras[i] = _retorno(quadro, _rotacionar(sinais, deslocamento), custos, config)

    melhores = int((amostras >= real).sum())
    return ResultadoPermutacao(
        retorno_real=real,
        retorno_medio_aleatorio=float(amostras.mean()),
        percentil_do_real=float((amostras < real).mean()),
        # O +1 no numerador e no denominador evita p igual a zero, que
        # sugeriria certeza impossivel com amostra finita.
        p_valor=(1 + melhores) / (1 + repeticoes),
        repeticoes=repeticoes,
    )


def sensibilidade_atraso(
    quadro: pd.DataFrame,
    sinais: pd.DataFrame,
    custos: ModeloCustos | None = None,
    config: ConfigExecucao | None = None,
    atrasos: tuple[int, ...] = (0, 1, 2, 3),
) -> pd.DataFrame:
    """Retorno em funcao de quantas barras se espera para executar.

    Leitura: `atraso=0` executa no fechamento da barra que gerou o sinal - e
    batota, esta ali so como referencia. Se o retorno despencar de 0 para 1 e
    virar ruido em 2, o sinal vivia do vazamento, nao do mercado.
    """
    custos = custos or ModeloCustos()
    base = config or ConfigExecucao()

    linhas = []
    for atraso in atrasos:
        variante = ConfigExecucao(
            max_barras_no_trade=base.max_barras_no_trade,
            atraso_barras=atraso,
            ambiguidade=base.ambiguidade,
            fracao_por_trade=base.fracao_por_trade,
        )
        resultado = executar(quadro, sinais, custos, variante)
        curva = resultado.curva_capital
        linhas.append(
            {
                "atraso_barras": atraso,
                "retorno": float(curva.iloc[-1] / curva.iloc[0] - 1),
                "trades": len(resultado.trades),
                "observacao": "referencia (executa no fechamento - batota)"
                if atraso == 0
                else "",
            }
        )
    return pd.DataFrame(linhas)


def sensibilidade_custo(
    quadro: pd.DataFrame,
    sinais: pd.DataFrame,
    custos: ModeloCustos | None = None,
    config: ConfigExecucao | None = None,
    multiplicadores: tuple[float, ...] = (0.0, 1.0, 2.0, 3.0),
) -> pd.DataFrame:
    base = custos or ModeloCustos()
    config = config or ConfigExecucao()

    linhas = []
    for fator in multiplicadores:
        variante = ModeloCustos(
            taxa_por_lado=base.taxa_por_lado * fator,
            slippage_por_lado=base.slippage_por_lado * fator,
        )
        resultado = executar(quadro, sinais, variante, config)
        curva = resultado.curva_capital
        linhas.append(
            {
                "custo_relativo": f"{fator:g}x",
                "custo_ida_e_volta": variante.custo_ida_e_volta,
                "retorno": float(curva.iloc[-1] / curva.iloc[0] - 1),
            }
        )
    return pd.DataFrame(linhas)
