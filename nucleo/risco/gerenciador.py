"""Dimensionamento de posicao pelo risco por trade.

A formula e a mesma do `RiskManager.py` original - orcamento de risco dividido
pela distancia ate o stop -, mas agora como funcao pura: sem ler ambiente e sem
logar no meio da conta, porque o backtest chama isso milhares de vezes.

Nada aqui envia ordem. O sistema mede e sugere; quem opera e voce.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ConfigRisco:
    saldo: float = 1000.0
    risco_por_trade: float = 0.02
    exposicao_maxima: float = 1.0
    quantidade_minima: float = 0.0
    passo_quantidade: float | None = None


@dataclass(frozen=True)
class Dimensionamento:
    quantidade: float
    valor_exposto: float
    risco_em_moeda: float
    distancia_stop: float
    distancia_stop_pct: float
    limitado_por_exposicao: bool
    viavel: bool
    observacao: str = ""


def config_do_ambiente() -> ConfigRisco:
    return ConfigRisco(
        saldo=float(os.getenv("SALDO_CONTA", "1000")),
        risco_por_trade=float(os.getenv("RISCO_MAX", "0.02")),
        exposicao_maxima=float(os.getenv("EXPOSICAO_MAXIMA", "1.0")),
    )


def dimensionar(
    preco_entrada: float, stop: float, config: ConfigRisco | None = None
) -> Dimensionamento:
    """Quanto comprar para que bater o stop custe exatamente o risco definido."""
    config = config or ConfigRisco()

    if not math.isfinite(preco_entrada) or preco_entrada <= 0:
        return _inviavel("preco de entrada invalido")
    if not math.isfinite(stop) or stop <= 0:
        return _inviavel("stop invalido")

    distancia = abs(preco_entrada - stop)
    if distancia == 0:
        return _inviavel("stop coincide com a entrada: risco por unidade seria zero")

    orcamento = config.saldo * config.risco_por_trade
    quantidade = orcamento / distancia
    valor = quantidade * preco_entrada

    # Um stop muito proximo pede uma posicao gigante para "arriscar so 2%".
    # Sem esse teto, um stop de 0,1% mandaria alavancar 20 vezes a conta.
    teto = config.saldo * config.exposicao_maxima
    limitado = valor > teto
    if limitado:
        valor = teto
        quantidade = valor / preco_entrada

    if config.passo_quantidade:
        quantidade = math.floor(quantidade / config.passo_quantidade) * config.passo_quantidade
        valor = quantidade * preco_entrada

    if quantidade <= config.quantidade_minima:
        return _inviavel(
            f"quantidade calculada ({quantidade:.8f}) nao alcanca o minimo negociavel"
        )

    return Dimensionamento(
        quantidade=quantidade,
        valor_exposto=valor,
        risco_em_moeda=min(orcamento, quantidade * distancia),
        distancia_stop=distancia,
        distancia_stop_pct=distancia / preco_entrada,
        limitado_por_exposicao=limitado,
        viavel=True,
        observacao=(
            "posicao cortada pelo teto de exposicao - o stop esta perto demais"
            if limitado
            else ""
        ),
    )


def _inviavel(motivo: str) -> Dimensionamento:
    return Dimensionamento(
        quantidade=0.0,
        valor_exposto=0.0,
        risco_em_moeda=0.0,
        distancia_stop=0.0,
        distancia_stop_pct=0.0,
        limitado_por_exposicao=False,
        viavel=False,
        observacao=motivo,
    )
