"""Calibra stop e alvo com excursao realizada, em vez de palpite.

Cada trade fechado guarda MFE e MAE - o quanto o preco andou a favor e contra
antes da saida. Eles respondem, com dado, duas perguntas que normalmente sao
chute:

- O stop esta apertado demais? Se os vencedores costumam chegar perto do stop
  antes de virar, um stop mais curto teria matado justamente eles.
- O alvo esta longe demais? Se os vencedores alcancam 3R e saem em 1,5R, metade
  do movimento capturado esta sendo devolvida.

**O limite honesto desta analise**: o caminho de cada trade so e conhecido ate a
saida dele. Para um trade que bateu o stop, nao sabemos o que o preco fez
depois - entao *alargar* o stop nao pode ser avaliado aqui. O mesmo vale para
afastar o alvo em trades que ja bateram o alvo. Por isso a tabela marca quanto
de cada celula e extrapolacao, e so as celulas com pouca extrapolacao sustentam
uma conclusao.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

COLUNAS_NECESSARIAS = {
    "preco_entrada", "stop", "alvo", "mfe_pct", "mae_pct",
    "multiplo_r", "motivo_saida", "retorno_liquido_pct",
}


def preparar(trades: pd.DataFrame) -> pd.DataFrame:
    """Converte excursao de percentual para multiplos de risco (R)."""
    faltando = COLUNAS_NECESSARIAS - set(trades.columns)
    if faltando:
        raise ValueError(f"Faltam colunas nos trades: {sorted(faltando)}")

    quadro = trades[trades["motivo_saida"] != "FIM_DADOS"].copy()
    quadro = quadro[quadro["stop"].notna() & (quadro["preco_entrada"] > 0)]

    risco = (quadro["preco_entrada"] - quadro["stop"]).abs() / quadro["preco_entrada"]
    quadro = quadro[risco > 0]
    risco = risco[risco > 0]

    quadro["risco_pct"] = risco
    quadro["mfe_r"] = quadro["mfe_pct"] / risco
    quadro["mae_r"] = quadro["mae_pct"] / risco
    quadro["alvo_r"] = (
        (quadro["alvo"] - quadro["preco_entrada"]).abs() / quadro["preco_entrada"] / risco
    )
    quadro["venceu"] = quadro["retorno_liquido_pct"] > 0
    return quadro


def resumo_excursao(quadro: pd.DataFrame) -> pd.DataFrame:
    """Como vencedores e perdedores se comportam antes de fechar."""
    linhas = []
    for rotulo, grupo in (
        ("vencedores", quadro[quadro["venceu"]]),
        ("perdedores", quadro[~quadro["venceu"]]),
    ):
        if grupo.empty:
            continue
        linhas.append(
            {
                "grupo": rotulo,
                "trades": len(grupo),
                "MAE mediana (R)": round(grupo["mae_r"].median(), 2),
                "MAE p90 (R)": round(grupo["mae_r"].quantile(0.10), 2),
                "MFE mediana (R)": round(grupo["mfe_r"].median(), 2),
                "MFE p90 (R)": round(grupo["mfe_r"].quantile(0.90), 2),
                "R realizado": round(grupo["multiplo_r"].median(), 2),
                "risco medio": f"{grupo['risco_pct'].mean():.2%}",
            }
        )
    return pd.DataFrame(linhas)


def aproveitamento(quadro: pd.DataFrame) -> dict:
    """Quanto da excursao favoravel virou resultado."""
    vencedores = quadro[quadro["venceu"]]
    perdedores = quadro[~quadro["venceu"]]

    with np.errstate(divide="ignore", invalid="ignore"):
        captura = (vencedores["multiplo_r"] / vencedores["mfe_r"]).replace(
            [np.inf, -np.inf], np.nan
        )

    return {
        "captura_mediana_vencedores": float(captura.median(skipna=True)),
        "perdedores_que_estiveram_positivos": float((perdedores["mfe_r"] > 0.5).mean())
        if len(perdedores)
        else float("nan"),
        "mfe_mediano_dos_perdedores": float(perdedores["mfe_r"].median())
        if len(perdedores)
        else float("nan"),
        "vencedores_que_quase_estoparam": float((vencedores["mae_r"] < -0.7).mean())
        if len(vencedores)
        else float("nan"),
    }


def tabela_calibragem(
    quadro: pd.DataFrame,
    stops: tuple[float, ...] = (0.4, 0.6, 0.8, 1.0),
    alvos: tuple[float, ...] = (1.0, 1.5, 2.0, 3.0),
) -> pd.DataFrame:
    """Expectancia aproximada para cada par (stop, alvo), medida em R.

    A convencao de ambiguidade e a mesma do motor: se a excursao alcancou os
    dois, assume-se o stop. Pessimista de proposito.
    """
    linhas = []
    for stop in stops:
        for alvo in alvos:
            stopado = quadro["mae_r"] <= -stop
            atingiu = quadro["mfe_r"] >= alvo

            resultado = np.where(
                stopado, -stop, np.where(atingiu, alvo, quadro["multiplo_r"])
            )

            # Extrapolacao: o caminho do trade so e conhecido ate a saida real.
            # Um stop mais largo que o original nao pode ser avaliado num trade
            # que foi estopado, nem um alvo mais distante num que bateu o alvo.
            extrapolado = ((stop > 1.0) & (quadro["motivo_saida"] == "STOP")) | (
                (alvo > quadro["alvo_r"]) & (quadro["motivo_saida"] == "ALVO")
            )

            acertos = (resultado > 0).sum()
            linhas.append(
                {
                    "stop_R": stop,
                    "alvo_R": alvo,
                    "expectancia_R": round(float(np.mean(resultado)), 3),
                    "acerto": f"{acertos / len(quadro):.0%}",
                    "extrapolado": f"{extrapolado.mean():.0%}",
                    "confiavel": "sim" if extrapolado.mean() <= 0.10 else "nao",
                }
            )
    return pd.DataFrame(linhas)


def relatorio(quadro: pd.DataFrame) -> str:
    dados = aproveitamento(quadro)
    linhas = [
        "APROVEITAMENTO",
        f"  vencedores capturaram {dados['captura_mediana_vencedores']:.0%} da "
        f"excursao favoravel (mediana)",
        f"  {dados['vencedores_que_quase_estoparam']:.0%} dos vencedores chegaram a "
        f"passar de 0,7R contra antes de virar",
        f"  {dados['perdedores_que_estiveram_positivos']:.0%} dos perdedores chegaram "
        f"a mais de 0,5R a favor antes de morrer "
        f"(mediana {dados['mfe_mediano_dos_perdedores']:+.2f}R)",
    ]

    if dados["vencedores_que_quase_estoparam"] > 0.30:
        linhas.append(
            "\n  !! Mais de 30% dos vencedores passaram perto do stop. Apertar o\n"
            "     stop aumentaria a taxa de acerto aparente e mataria os ganhos."
        )
    if dados["perdedores_que_estiveram_positivos"] > 0.50:
        linhas.append(
            "\n  !! Mais da metade dos perdedores esteve com lucro relevante antes de\n"
            "     virar. Vale testar saida parcial ou stop movel - o backtest mede."
        )
    return "\n".join(linhas)
