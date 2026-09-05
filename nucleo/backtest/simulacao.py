"""Traduz o resultado dos setups para dinheiro, com uma banca de verdade.

Percentual e multiplo de risco medem o sinal, mas nao respondem a pergunta que
importa na pratica: comecando com R$100, quanto sobra no fim?

Duas coisas que so aparecem quando se conta em dinheiro:

**Ordem minima.** Corretora nao aceita ordem de qualquer tamanho. Com banca
pequena e risco de 2%, boa parte dos trades simplesmente nao poderia ter sido
executada - e um backtest que ignora isso mostra lucro de operacoes que a
corretora teria recusado.

**Juros compostos com risco fixo.** Arriscar 2% da banca ATUAL faz a posicao
encolher junto com o prejuizo, o que suaviza a queda e desacelera a
recuperacao. Aplicar 2% da banca inicial em tudo produz numero diferente, e
mais bonito do que a realidade.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class ConfigBanca:
    inicial: float = 100.0
    risco_por_trade: float = 0.02
    exposicao_maxima: float = 1.0
    # Valor minimo que a corretora aceita por ordem, na moeda da cotacao.
    # A OKX aceita a partir de cerca de 1 USDT em spot; a Binance, 5.
    valor_minimo_ordem: float = 1.0
    moeda: str = "USDT"


@dataclass
class ResultadoBanca:
    config: ConfigBanca
    saldo_final: float
    executados: int
    recusados_por_minimo: int
    maior_rebaixamento: float
    curva: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    detalhe: pd.DataFrame = field(default_factory=pd.DataFrame)

    @property
    def retorno(self) -> float:
        return self.saldo_final / self.config.inicial - 1

    @property
    def pct_recusado(self) -> float:
        total = self.executados + self.recusados_por_minimo
        return self.recusados_por_minimo / total if total else 0.0


def simular(trades: pd.DataFrame, config: ConfigBanca | None = None) -> ResultadoBanca:
    """Percorre os trades em ordem e acompanha a banca."""
    config = config or ConfigBanca()

    if trades.empty:
        return ResultadoBanca(config, config.inicial, 0, 0, 0.0)

    fechados = trades[trades["motivo_saida"] != "FIM_DADOS"].copy()
    if "entrada" in fechados.columns:
        fechados = fechados.sort_values("entrada")

    saldo = config.inicial
    pico = saldo
    rebaixamento = 0.0
    executados = recusados = 0
    curva, linhas = [], []

    for trade in fechados.itertuples():
        distancia = abs(trade.preco_entrada - trade.stop) / trade.preco_entrada
        if not np.isfinite(distancia) or distancia <= 0:
            continue

        # Risco sobre a banca ATUAL, nao sobre a inicial: e assim que funciona
        # de verdade, e e o que faz a curva desacelerar depois de uma sequencia
        # ruim em vez de recuperar em linha reta.
        orcamento = saldo * config.risco_por_trade
        valor = min(orcamento / distancia, saldo * config.exposicao_maxima)

        if valor < config.valor_minimo_ordem:
            recusados += 1
            continue

        fracao = valor / saldo
        resultado = saldo * trade.retorno_liquido_pct * fracao
        saldo += resultado
        executados += 1

        pico = max(pico, saldo)
        rebaixamento = min(rebaixamento, saldo / pico - 1)
        curva.append(saldo)
        linhas.append(
            {
                "entrada": getattr(trade, "entrada", None),
                "par": getattr(trade, "par", ""),
                "valor_ordem": round(valor, 2),
                "risco": round(orcamento, 2),
                "resultado": round(resultado, 2),
                "saldo": round(saldo, 2),
            }
        )

        if saldo <= 0:
            break

    return ResultadoBanca(
        config=config,
        saldo_final=saldo,
        executados=executados,
        recusados_por_minimo=recusados,
        maior_rebaixamento=rebaixamento,
        curva=pd.Series(curva, dtype=float),
        detalhe=pd.DataFrame(linhas),
    )


def relatorio(resultado: ResultadoBanca, rotulo: str = "") -> str:
    config = resultado.config
    linhas = [
        f"  banca inicial       {config.moeda} {config.inicial:,.2f}",
        f"  saldo final         {config.moeda} {resultado.saldo_final:,.2f}"
        f"   ({resultado.retorno:+.1%})",
        f"  trades executados   {resultado.executados}",
        f"  rebaixamento maximo {resultado.maior_rebaixamento:.1%}",
    ]

    if resultado.recusados_por_minimo:
        linhas.append(
            f"  !! {resultado.recusados_por_minimo} trades "
            f"({resultado.pct_recusado:.0%}) ficaram abaixo da ordem minima de "
            f"{config.moeda} {config.valor_minimo_ordem:g} e NAO teriam sido "
            f"executados"
        )
    if rotulo:
        linhas.insert(0, f"  {rotulo}")
    return "\n".join(linhas)


def comparar(
    trades_por_setup: dict[str, pd.DataFrame], config: ConfigBanca | None = None
) -> pd.DataFrame:
    """Uma linha por setup: quanto a mesma banca vira em cada um."""
    config = config or ConfigBanca()
    linhas = []
    for nome, trades in trades_por_setup.items():
        resultado = simular(trades, config)
        linhas.append(
            {
                "setup": nome,
                "saldo_final": round(resultado.saldo_final, 2),
                "retorno": f"{resultado.retorno:+.1%}",
                "trades": resultado.executados,
                "recusados": resultado.recusados_por_minimo,
                "DD_max": f"{resultado.maior_rebaixamento:.1%}",
            }
        )
    return pd.DataFrame(linhas).sort_values("saldo_final", ascending=False)
