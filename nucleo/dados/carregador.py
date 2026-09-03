"""Orquestra cache e rede para entregar velas prontas para analise.

Quem chama pede um periodo e recebe velas; se faltava pedaco no banco, ele foi
buscado e gravado no caminho. Backtests seguintes rodam offline, o que e o que
torna o resultado reproduzivel.
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd

from .armazenamento import Armazenamento
from .provedor import (
    DURACAO_MS,
    ProvedorDados,
    agora_ms,
    duracao_ms,
    para_ms,
    quadro_vazio,
    reamostrar,
)


def escolher_base(provedor: ProvedorDados, timeframe: str) -> str:
    """Menor unidade que a corretora entrega e que compoe o timeframe pedido.

    Se a corretora tem o timeframe nativo, usa ele. Senao procura o maior
    divisor exato disponivel - 4h sai de 1h, por exemplo.
    """
    nativos = getattr(provedor, "timeframes_nativos", lambda: frozenset())()
    if not nativos or timeframe in nativos:
        return timeframe

    alvo = duracao_ms(timeframe)
    divisores = [
        candidato
        for candidato in nativos
        if candidato in DURACAO_MS
        and duracao_ms(candidato) < alvo
        and alvo % duracao_ms(candidato) == 0
    ]
    if not divisores:
        raise ValueError(
            f"{provedor.nome} nao tem {timeframe} nem um timeframe menor que o componha."
        )
    return max(divisores, key=duracao_ms)


def carregar(
    par: str,
    timeframe: str,
    inicio: datetime | str | int | None = None,
    fim: datetime | str | int | None = None,
    *,
    provedor: ProvedorDados,
    armazenamento: Armazenamento,
    barras_aquecimento: int = 0,
    usar_rede: bool = True,
) -> pd.DataFrame:
    """Velas fechadas do periodo, ja no timeframe pedido.

    `barras_aquecimento` estende o inicio para tras. Sem isso as primeiras
    barras saem com indicador NaN e o backtest comeca tarde em silencio - foi
    exatamente o que aconteceu no codigo antigo, que pedia 100 velas e
    calculava media de 200, deixando a media longa NaN para sempre.
    """
    base = escolher_base(provedor, timeframe)
    passo_alvo = duracao_ms(timeframe)

    fim_ms = para_ms(fim) or agora_ms()
    inicio_ms = para_ms(inicio)
    if inicio_ms is None:
        inicio_ms = fim_ms - 365 * 24 * 60 * 60 * 1000
    inicio_ms -= barras_aquecimento * passo_alvo

    if usar_rede:
        _preencher_lacunas(
            par, base, inicio_ms, fim_ms, provedor=provedor, armazenamento=armazenamento
        )

    velas = armazenamento.carregar_velas(
        provedor.nome, par, base, inicio=inicio_ms, fim=fim_ms
    )
    if velas.empty:
        return quadro_vazio()

    if base != timeframe:
        velas = reamostrar(velas, base, timeframe)

    return velas


def _preencher_lacunas(
    par: str,
    timeframe: str,
    inicio_ms: int,
    fim_ms: int,
    *,
    provedor: ProvedorDados,
    armazenamento: Armazenamento,
) -> int:
    """Busca na corretora apenas o que o banco ainda nao tem."""
    faltantes = armazenamento.faixas_faltantes(
        provedor.nome, par, timeframe, inicio_ms, fim_ms
    )
    # A cobertura nunca avanca alem da ultima vela ja fechada. Se ela chegasse
    # ate "agora", a faixa da vela em formacao ficaria marcada como baixada e
    # as velas novas nunca mais seriam buscadas - o cache congelaria no tempo.
    limite_seguro = agora_ms() - duracao_ms(timeframe)

    total = 0
    for faixa_inicio, faixa_fim in faltantes:
        quadro = provedor.obter_velas(par, timeframe, inicio=faixa_inicio, fim=faixa_fim)
        total += armazenamento.salvar_velas(provedor.nome, par, timeframe, quadro)

        # Anotada mesmo com retorno vazio: significa que a faixa foi consultada
        # e a corretora nao tem nada ali. Sem isso, um periodo anterior a
        # listagem do par seria reconsultado a cada execucao, para sempre.
        fim_anotado = min(faixa_fim, limite_seguro)
        if fim_anotado > faixa_inicio:
            armazenamento.registrar_cobertura(
                provedor.nome, par, timeframe, faixa_inicio, fim_anotado
            )
    return total
