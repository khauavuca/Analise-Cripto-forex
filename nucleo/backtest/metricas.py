"""Metricas de desempenho - e os avisos que impedem elas de mentir.

A meta declarada do projeto e "assertividade". A correcao mais importante deste
modulo e que **a metrica pedida e a mais capaz de enganar**:

- Taxa de acerto sem payoff nao significa nada. 90% de acerto com payoff 1:20 e
  uma estrategia perdedora; 35% com 1:4 e excelente.
- Taxa de acerto e trivialmente maximizavel: aproxime o alvo e afaste o stop, e
  voce chega a 95% de acerto com uma unica perda que apaga o ano.
- Taxa de acerto e instavel: com 40 trades, 55% observados tem intervalo de
  confianca de aproximadamente mais ou menos 15 pontos.

Por isso o relatorio sempre imprime acerto, intervalo de Wilson, payoff e
expectancia juntos, e carimba AMOSTRA INSUFICIENTE abaixo de 30 trades.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from ..dados.provedor import duracao_ms

MINIMO_PARA_CONCLUIR = 30
MINIMO_PARA_CONFIAR = 100
MS_POR_ANO = 365 * 24 * 60 * 60 * 1000


@dataclass
class Metricas:
    n_trades: int
    taxa_acerto: float
    acerto_ic_baixo: float
    acerto_ic_alto: float
    payoff: float
    expectancia_r: float
    expectancia_pct: float
    fator_lucro: float
    retorno_total: float
    retorno_buy_hold: float
    rebaixamento_maximo: float
    barras_em_rebaixamento: int
    sharpe: float
    sortino: float
    barras_medias_no_trade: float
    maior_sequencia_perdas: int
    trades_por_mes: float
    amostra_suficiente: bool

    def para_dicionario(self) -> dict:
        return asdict(self)


def intervalo_wilson(sucessos: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """Intervalo de confianca de 95% para uma proporcao.

    Wilson e nao a formula normal simples porque com poucos trades - que e
    sempre o caso em swing - a aproximacao normal produz intervalos que saem
    de [0, 1] e subestima a incerteza justamente onde ela mais importa.
    """
    if total == 0:
        return (math.nan, math.nan)

    proporcao = sucessos / total
    denominador = 1 + z**2 / total
    centro = (proporcao + z**2 / (2 * total)) / denominador
    margem = (
        z * math.sqrt(proporcao * (1 - proporcao) / total + z**2 / (4 * total**2))
    ) / denominador
    return (max(0.0, centro - margem), min(1.0, centro + margem))


def calcular(
    trades: pd.DataFrame,
    curva_capital: pd.Series,
    quadro: pd.DataFrame,
    timeframe: str,
) -> Metricas:
    fechados = trades[trades["motivo_saida"] != "FIM_DADOS"] if not trades.empty else trades
    total = len(fechados)

    if total == 0:
        vazio = float("nan")
        return Metricas(
            0, vazio, vazio, vazio, vazio, vazio, vazio, vazio,
            _retorno_total(curva_capital), retorno_comprar_e_segurar(quadro),
            *_rebaixamento(curva_capital), vazio, vazio, vazio, 0, 0.0, False,
        )

    retornos = fechados["retorno_liquido_pct"]
    ganhadores = retornos[retornos > 0]
    perdedores = retornos[retornos <= 0]

    acertos = int(len(ganhadores))
    taxa_acerto = acertos / total
    ic_baixo, ic_alto = intervalo_wilson(acertos, total)

    ganho_medio = float(ganhadores.mean()) if len(ganhadores) else 0.0
    perda_media = float(abs(perdedores.mean())) if len(perdedores) else 0.0
    payoff = ganho_medio / perda_media if perda_media > 0 else math.inf

    soma_ganhos = float(ganhadores.sum())
    soma_perdas = float(abs(perdedores.sum()))
    fator_lucro = soma_ganhos / soma_perdas if soma_perdas > 0 else math.inf

    rebaixamento, barras_rebaixado = _rebaixamento(curva_capital)
    barras_por_ano = MS_POR_ANO / duracao_ms(timeframe)
    meses = len(quadro) * duracao_ms(timeframe) / (MS_POR_ANO / 12)

    return Metricas(
        n_trades=total,
        taxa_acerto=taxa_acerto,
        acerto_ic_baixo=ic_baixo,
        acerto_ic_alto=ic_alto,
        payoff=payoff,
        # Expectancia em R, nao em reais: em moeda o numero fica contaminado
        # pelo tamanho da posicao; em R ele mede o sinal.
        expectancia_r=float(fechados["multiplo_r"].mean(skipna=True)),
        expectancia_pct=float(retornos.mean()),
        fator_lucro=fator_lucro,
        retorno_total=_retorno_total(curva_capital),
        retorno_buy_hold=retorno_comprar_e_segurar(quadro),
        rebaixamento_maximo=rebaixamento,
        barras_em_rebaixamento=barras_rebaixado,
        sharpe=_sharpe(curva_capital, barras_por_ano),
        sortino=_sortino(curva_capital, barras_por_ano),
        barras_medias_no_trade=float(fechados["barras_no_trade"].mean()),
        maior_sequencia_perdas=_maior_sequencia_perdas(retornos),
        trades_por_mes=total / meses if meses > 0 else 0.0,
        amostra_suficiente=total >= MINIMO_PARA_CONCLUIR,
    )


def _retorno_total(curva: pd.Series) -> float:
    if curva.empty:
        return math.nan
    return float(curva.iloc[-1] / curva.iloc[0] - 1)


def retorno_comprar_e_segurar(quadro: pd.DataFrame) -> float:
    """Comparacao obrigatoria: em janela de alta quase tudo parece genial."""
    if quadro.empty:
        return math.nan
    return float(quadro["fechamento"].iloc[-1] / quadro["abertura"].iloc[0] - 1)


def _rebaixamento(curva: pd.Series) -> tuple[float, int]:
    if curva.empty:
        return (math.nan, 0)
    topo = curva.cummax()
    queda = curva / topo - 1
    abaixo = (curva < topo).to_numpy()

    maior, atual = 0, 0
    for marcado in abaixo:
        atual = atual + 1 if marcado else 0
        maior = max(maior, atual)
    return (float(queda.min()), int(maior))


def _retornos_por_barra(curva: pd.Series) -> pd.Series:
    return curva.pct_change().replace([np.inf, -np.inf], np.nan).dropna()


def _sharpe(curva: pd.Series, barras_por_ano: float) -> float:
    """Anualizado pelos retornos por barra da curva de capital.

    O fator de anualizacao precisa casar com o timeframe: 1h usa
    sqrt(24*365), 4h usa sqrt(6*365). Fator errado e a causa numero um de
    Sharpe reportado em 8.
    """
    retornos = _retornos_por_barra(curva)
    desvio = float(retornos.std(ddof=1))
    if len(retornos) < 2 or desvio == 0:
        return math.nan
    return float(retornos.mean() / desvio * math.sqrt(barras_por_ano))


def _sortino(curva: pd.Series, barras_por_ano: float) -> float:
    retornos = _retornos_por_barra(curva)
    if len(retornos) < 2:
        return math.nan
    negativos = retornos.clip(upper=0)
    desvio_baixo = float(np.sqrt((negativos**2).mean()))
    if desvio_baixo == 0:
        # Sem nenhum periodo negativo o Sortino e infinito, o que nao e nota
        # boa - e amostra pequena. Melhor devolver vazio que imprimir "inf".
        return math.nan
    return float(retornos.mean() / desvio_baixo * math.sqrt(barras_por_ano))


def _maior_sequencia_perdas(retornos: pd.Series) -> int:
    maior, atual = 0, 0
    for retorno in retornos:
        atual = atual + 1 if retorno <= 0 else 0
        maior = max(maior, atual)
    return int(maior)


def _descrever_dimensionamento(diagnosticos: dict) -> str:
    """Explica sobre qual base o retorno foi calculado.

    Sem isso o retorno total fica incomparavel: arriscar 2% por trade e
    alocar 100% do capital produzem numeros de ordem completamente diferente
    a partir dos mesmos sinais.
    """
    if diagnosticos.get("dimensionamento") == "fixo":
        return "fracao fixa do capital por trade"
    risco = diagnosticos.get("risco_por_trade", 0.0)
    fracao = diagnosticos.get("fracao_media", 0.0)
    return (
        f"por risco - {risco:.1%} do capital por trade "
        f"(exposicao media {fracao:.0%})"
    )


def formatar_relatorio(
    metricas: Metricas, diagnosticos: dict, titulo: str = "Backtest"
) -> str:
    """Relatorio de texto. Acerto nunca aparece sozinho."""
    linhas = [f"=== {titulo} ===", ""]

    if metricas.n_trades == 0:
        linhas.append("Nenhum trade fechado no periodo - nada a medir.")
        return "\n".join(linhas)

    if not metricas.amostra_suficiente:
        linhas += [
            f"!! AMOSTRA INSUFICIENTE: {metricas.n_trades} trades.",
            f"   Abaixo de {MINIMO_PARA_CONCLUIR} nada aqui e conclusivo; para agir "
            f"com base no numero, mire {MINIMO_PARA_CONFIAR}+.",
            "",
        ]

    linhas += [
        "DECISAO (e por aqui que se julga a estrategia)",
        f"  expectancia          {metricas.expectancia_r:+.3f} R por trade",
        f"  fator de lucro       {metricas.fator_lucro:.2f}"
        f"   {'(ganha mais do que perde)' if metricas.fator_lucro > 1 else '(perde mais do que ganha)'}",
        f"  payoff medio         {metricas.payoff:.2f} : 1",
        f"  retorno liquido      {metricas.retorno_total:+.1%}"
        f"   |  buy & hold: {metricas.retorno_buy_hold:+.1%}",
        "",
        "ACERTO (nunca leia sem o payoff acima)",
        f"  taxa de acerto       {metricas.taxa_acerto:.1%}"
        f"   (IC 95%: {metricas.acerto_ic_baixo:.1%} a {metricas.acerto_ic_alto:.1%})",
        f"  trades               {metricas.n_trades}"
        f"   ({metricas.trades_por_mes:.1f} por mes)",
        "",
        "RISCO",
        f"  rebaixamento maximo  {metricas.rebaixamento_maximo:.1%}"
        f"   ({metricas.barras_em_rebaixamento} barras abaixo do topo)",
        f"  maior sequencia de perdas  {metricas.maior_sequencia_perdas}",
        f"  sharpe / sortino     {metricas.sharpe:.2f} / {metricas.sortino:.2f}",
        f"  barras por trade     {metricas.barras_medias_no_trade:.1f}",
        "",
        "DIAGNOSTICO DA SIMULACAO",
        f"  dimensionamento      {_descrever_dimensionamento(diagnosticos)}",
        f"  custo ida e volta    {diagnosticos.get('custo_ida_e_volta', 0):.2%} por trade",
        f"  saidas ambiguas      {diagnosticos.get('pct_saidas_ambiguas', 0):.1%}"
        f"   (barra tocou stop e alvo juntos)",
    ]

    ambiguo = diagnosticos.get("pct_saidas_ambiguas", 0)
    if ambiguo > 0.25:
        linhas.append(
            f"  !! {ambiguo:.0%} das saidas sao ambiguas: stop e alvo cabem os dois "
            "dentro da barra tipica.\n"
            "     Nesse regime nenhuma convencao resolve - o backtest nao consegue "
            "julgar esta estrategia\n     neste timeframe. Afaste os niveis ou desca "
            "o timeframe da simulacao."
        )

    if metricas.expectancia_r <= 0:
        linhas += [
            "",
            "VEREDICTO: expectancia nao positiva. Depois de custos, esta estrategia "
            "perde dinheiro no periodo testado.",
        ]
    elif metricas.retorno_total < metricas.retorno_buy_hold:
        linhas += [
            "",
            "VEREDICTO: lucrativa, porem abaixo de simplesmente comprar e segurar "
            "no mesmo periodo.",
        ]

    return "\n".join(linhas)
