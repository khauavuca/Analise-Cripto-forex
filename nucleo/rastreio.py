"""Acompanha cada recomendacao coletada ate o desfecho dela.

E o teste mais limpo que existe neste projeto. No backtest, quem escolheu os
pares fui eu, quem escolheu o periodo fui eu, e quem escolheu os parametros fui
eu - e cada uma dessas escolhas empresta um pouco de otimismo ao resultado.
Aqui nao ha escolha nenhuma: o sinal apareceu ao vivo, foi gravado com os
niveis dele, e o mercado decidiu sozinho o que aconteceu depois.

O desfecho e reconstruido **pelo mesmo motor do backtest**, de proposito. Nao
existe uma segunda implementacao de "quando o trade fecha" para divergir da
primeira: mesma regra de executar na abertura seguinte, mesma convencao de
ambiguidade quando a vela toca stop e alvo, mesmos custos.

Um sinal cujo desfecho ainda nao chegou fica marcado como aberto e sai da
conta - contar posicao aberta como acerto e a forma mais facil de inflar
assertividade sem perceber.
"""
from __future__ import annotations

from datetime import timedelta

import numpy as np
import pandas as pd

from .backtest import metricas as met
from .backtest.motor import MOTIVO_FIM, ConfigExecucao, ModeloCustos, executar
from .dados.carregador import carregar
from .dados.provedor import duracao_ms
from .estrategias.base import quadro_sinais

COLUNAS_MINIMAS = {"par", "timeframe", "estrategia", "vela", "direcao"}


def preparar_observacoes(quadro: pd.DataFrame) -> pd.DataFrame:
    """Deixa so o que e sinal aproveitavel, com os tipos certos."""
    faltando = COLUNAS_MINIMAS - set(quadro.columns)
    if faltando:
        raise ValueError(f"Faltam colunas nas observacoes: {sorted(faltando)}")

    sinais = quadro.copy()
    sinais["vela"] = pd.to_datetime(sinais["vela"], utc=True, format="mixed")
    sinais["direcao"] = pd.to_numeric(sinais["direcao"], errors="coerce").fillna(0)
    for coluna in ("stop", "alvo", "forca"):
        if coluna not in sinais.columns:
            sinais[coluna] = np.nan
        sinais[coluna] = pd.to_numeric(sinais[coluna], errors="coerce")

    sinais = sinais[sinais["direcao"] != 0]
    # Sem stop e alvo nao ha o que acompanhar: nao existe criterio de saida.
    sinais = sinais[sinais["stop"].notna() & sinais["alvo"].notna()]
    return sinais.drop_duplicates(subset=["par", "timeframe", "estrategia", "vela"])


def _montar_quadro_de_sinais(
    indice: pd.DatetimeIndex, registros: pd.DataFrame
) -> pd.DataFrame:
    """Coloca cada sinal gravado na barra em que ele nasceu."""
    sinais = quadro_sinais(indice)
    for registro in registros.itertuples():
        if registro.vela not in indice:
            continue
        sinais.loc[registro.vela, "direcao"] = np.int8(registro.direcao)
        sinais.loc[registro.vela, "forca"] = (
            0.0 if pd.isna(registro.forca) else float(registro.forca)
        )
        sinais.loc[registro.vela, "stop"] = float(registro.stop)
        sinais.loc[registro.vela, "alvo"] = float(registro.alvo)
        sinais.loc[registro.vela, "motivo"] = "sinal coletado ao vivo"
    return sinais


def rastrear(
    observacoes: pd.DataFrame,
    provedor,
    armazenamento,
    custos: ModeloCustos | None = None,
    config: ConfigExecucao | None = None,
    usar_rede: bool = True,
) -> pd.DataFrame:
    """Devolve um quadro de trades, um por sinal com desfecho conhecido."""
    custos = custos or ModeloCustos()
    config = config or ConfigExecucao()
    sinais = preparar_observacoes(observacoes)
    if sinais.empty:
        return pd.DataFrame()

    resultados = []
    for (par, timeframe), grupo in sinais.groupby(["par", "timeframe"], sort=True):
        passo = duracao_ms(timeframe)
        # Uma folga antes do primeiro sinal e depois do ultimo: o motor precisa
        # da barra seguinte para executar, e do caminho ate a saida.
        inicio = grupo["vela"].min() - timedelta(milliseconds=passo * 5)
        try:
            quadro = carregar(
                par, timeframe, inicio,
                provedor=provedor, armazenamento=armazenamento, usar_rede=usar_rede,
            )
        except Exception as erro:
            print(f"  ! {par} {timeframe}: {erro}")
            continue
        if quadro.empty:
            continue

        for nome, registros in grupo.groupby("estrategia", sort=True):
            resultado = executar(
                quadro, _montar_quadro_de_sinais(quadro.index, registros), custos, config
            )
            if resultado.trades.empty:
                continue
            trades = resultado.trades.copy()
            trades["estrategia"] = nome
            trades["par"] = par
            trades["timeframe"] = timeframe
            # FIM_DADOS aqui nao e "fim do arquivo", e "ainda nao terminou".
            trades["aberto"] = trades["motivo_saida"] == MOTIVO_FIM
            resultados.append(trades)

    return (
        pd.concat(resultados, ignore_index=True) if resultados else pd.DataFrame()
    )


def resumir(trades: pd.DataFrame, por: str = "estrategia") -> pd.DataFrame:
    """Assertividade por setup, contando so o que ja fechou."""
    if trades.empty:
        return pd.DataFrame()

    linhas = []
    for chave, grupo in trades.groupby(por, sort=True):
        fechados = grupo[~grupo["aberto"]]
        abertos = int(grupo["aberto"].sum())
        if fechados.empty:
            linhas.append(
                {por: chave, "fechados": 0, "abertos": abertos, "situacao": "sem desfecho"}
            )
            continue

        retornos = fechados["retorno_liquido_pct"]
        ganhos = retornos[retornos > 0]
        perdas = retornos[retornos <= 0]
        baixo, alto = met.intervalo_wilson(len(ganhos), len(fechados))

        linhas.append(
            {
                por: chave,
                "fechados": len(fechados),
                "abertos": abertos,
                "acerto": f"{len(ganhos) / len(fechados):.0%}",
                "IC95": f"{baixo:.0%}-{alto:.0%}",
                "payoff": round(ganhos.mean() / abs(perdas.mean()), 2) if len(perdas) else np.inf,
                "expect_R": round(float(fechados["multiplo_r"].mean()), 3),
                "fator_lucro": (
                    round(ganhos.sum() / abs(perdas.sum()), 2) if len(perdas) else np.inf
                ),
                "situacao": (
                    "ok" if len(fechados) >= met.MINIMO_PARA_CONCLUIR else "AMOSTRA CURTA"
                ),
            }
        )
    return pd.DataFrame(linhas)


def texto_do_veredito(trades: pd.DataFrame) -> str:
    """O aviso que precisa acompanhar qualquer numero daqui."""
    if trades.empty:
        return (
            "Nenhum sinal com desfecho ainda. Isso e o esperado no comeco: os\n"
            "setups sao seletivos e um trade leva horas para bater stop ou alvo."
        )

    fechados = int((~trades["aberto"]).sum())
    abertos = int(trades["aberto"].sum())
    linhas = [
        f"{fechados} sinais com desfecho, {abertos} ainda em aberto "
        f"(estes ficam fora da conta).",
    ]
    if fechados < met.MINIMO_PARA_CONCLUIR:
        linhas.append(
            f"Abaixo de {met.MINIMO_PARA_CONCLUIR} trades nada aqui e conclusivo. "
            f"Para agir com base no numero, mire {met.MINIMO_PARA_CONFIAR}+."
        )
    linhas.append(
        "Estes dados valem justamente porque ninguem mexeu neles. Ajustar\n"
        "parametros ate o numero ficar bom e depois reportar esse numero e o\n"
        "mesmo mecanismo que ja derrubou a confluencia e o ETH neste projeto:\n"
        "calibre no historico, e use este conjunto uma vez so, como veredito."
    )
    return "\n".join(linhas)
