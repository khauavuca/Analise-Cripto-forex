"""Coleta ao vivo, com dados reais e sem enviar ordem.

Grava o estado completo de cada vela que fecha - preco, todos os indicadores da
estrategia e a decisao - em `observacoes`. Registrar tambem as barras sem sinal
e proposital: sem elas, uma barra que nao disparou vira caixa preta, e nao da
para saber se o gatilho passou perto ou nem chegou perto.

No fim roda a **checagem de paridade**: recalcula a mesma janela pelo caminho
do backtest e compara com o que foi decidido ao vivo. E o unico teste que
prova que os dois caminhos concordam - a divergencia entre eles e a forma mais
comum de um "backtest de 62%" virar um sistema de 40% na conta real.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import pandas as pd

from .dados.armazenamento import Armazenamento
from .dados.carregador import carregar
from .dados.provedor import duracao_ms
from .estrategias.base import Estrategia


@dataclass
class ResumoColeta:
    velas_novas: int = 0
    ciclos: int = 0
    erros: int = 0
    sinais: list[dict] = field(default_factory=list)
    por_alvo: dict[tuple[str, str], int] = field(default_factory=dict)


def _ultima_vela(quadro: pd.DataFrame, sinais: pd.DataFrame, painel: pd.DataFrame):
    momento = quadro.index[-1]
    vela = quadro.iloc[-1]
    sinal = sinais.iloc[-1]
    indicadores = (
        {} if painel.empty else {c: painel.iloc[-1][c] for c in painel.columns}
    )
    return momento, vela, sinal, indicadores


def coletar(
    pares: list[str],
    timeframes: list[str],
    estrategia: Estrategia,
    provedor,
    armazenamento: Armazenamento,
    minutos: int,
    intervalo_segundos: int = 20,
    ao_registrar=None,
) -> ResumoColeta:
    """Acompanha os pares pelo tempo pedido, gravando cada vela fechada."""
    fim = datetime.now(timezone.utc) + timedelta(minutes=minutos)
    aquecimento = estrategia.barras_de_aquecimento()
    resumo = ResumoColeta()

    while datetime.now(timezone.utc) < fim:
        resumo.ciclos += 1
        for par in pares:
            for timeframe in timeframes:
                try:
                    quadro = carregar(
                        par,
                        timeframe,
                        datetime.now(timezone.utc)
                        - timedelta(
                            milliseconds=duracao_ms(timeframe) * (aquecimento + 60)
                        ),
                        provedor=provedor,
                        armazenamento=armazenamento,
                        barras_aquecimento=aquecimento,
                    )
                    if quadro.empty:
                        continue

                    sinais = estrategia.gerar_sinais(quadro)
                    painel = estrategia.painel_indicadores(quadro)
                    momento, vela, sinal, indicadores = _ultima_vela(
                        quadro, sinais, painel
                    )

                    nova = armazenamento.registrar_observacao(
                        provedor.nome,
                        par,
                        timeframe,
                        estrategia.nome,
                        int(momento.timestamp() * 1000),
                        vela.to_dict(),
                        {
                            "direcao": int(sinal.direcao),
                            "forca": float(sinal.forca),
                            "stop": None if pd.isna(sinal.stop) else float(sinal.stop),
                            "alvo": None if pd.isna(sinal.alvo) else float(sinal.alvo),
                            "motivo": str(sinal.motivo),
                        },
                        indicadores,
                    )

                    if nova:
                        resumo.velas_novas += 1
                        chave = (par, timeframe)
                        resumo.por_alvo[chave] = resumo.por_alvo.get(chave, 0) + 1
                        registro = {
                            "par": par,
                            "timeframe": timeframe,
                            "vela": momento,
                            "fechamento": float(vela.fechamento),
                            "direcao": int(sinal.direcao),
                            "forca": float(sinal.forca),
                        }
                        if int(sinal.direcao) != 0:
                            resumo.sinais.append(registro)
                        if ao_registrar:
                            ao_registrar(registro)

                except Exception as erro:
                    resumo.erros += 1
                    if ao_registrar:
                        ao_registrar({"erro": f"{par} {timeframe}: {erro}"})

        restante = (fim - datetime.now(timezone.utc)).total_seconds()
        if restante <= 0:
            break
        time.sleep(min(intervalo_segundos, restante))

    return resumo


def conferir_paridade(
    pares: list[str],
    timeframes: list[str],
    estrategia: Estrategia,
    provedor,
    armazenamento: Armazenamento,
) -> pd.DataFrame:
    """Recalcula em lote e compara com o que foi decidido ao vivo.

    Ao vivo, cada decisao foi tomada com a serie que existia naquele instante.
    Aqui a serie inteira e recalculada de uma vez, como no backtest. Se algum
    valor mudar, o caminho ao vivo e o simulado nao sao o mesmo sistema.
    """
    linhas = []
    aquecimento = estrategia.barras_de_aquecimento()

    for par in pares:
        for timeframe in timeframes:
            gravadas = armazenamento.observacoes(par, timeframe)
            gravadas = gravadas[gravadas.estrategia == estrategia.nome]
            if gravadas.empty:
                continue

            quadro = carregar(
                par,
                timeframe,
                datetime.now(timezone.utc)
                - timedelta(milliseconds=duracao_ms(timeframe) * (aquecimento + 200)),
                provedor=provedor,
                armazenamento=armazenamento,
                barras_aquecimento=aquecimento,
                usar_rede=False,
            )
            if quadro.empty:
                continue

            sinais = estrategia.gerar_sinais(quadro)

            # DatetimeIndex e nao Series: `.values` de uma Series com fuso
            # devolve datetime64 sem fuso, e a busca no indice nao casa nada.
            momentos = pd.DatetimeIndex(
                pd.to_datetime(gravadas.vela_ms, unit="ms", utc=True)
            )
            presentes = momentos.isin(sinais.index)
            comuns = momentos[presentes]
            if len(comuns) == 0:
                continue

            ao_vivo = gravadas.loc[presentes, "direcao"].to_numpy(dtype=int)
            em_lote = sinais.loc[comuns, "direcao"].to_numpy(dtype=int)
            divergentes = int((ao_vivo != em_lote).sum())

            linhas.append(
                {
                    "par": par,
                    "timeframe": timeframe,
                    "velas_conferidas": len(comuns),
                    "divergencias": divergentes,
                    "situacao": "OK" if divergentes == 0 else "DIVERGE",
                }
            )

    return pd.DataFrame(linhas)
