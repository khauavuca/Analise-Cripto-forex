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
    estrategias: list[Estrategia],
    provedor,
    armazenamento: Armazenamento,
    minutos: int,
    intervalo_segundos: int = 20,
    ao_registrar=None,
    parar=None,
    ciclos_por_pulso: int = 60,
) -> ResumoColeta:
    """Acompanha os pares pelo tempo pedido, gravando cada vela fechada.

    Varias estrategias veem a MESMA vela: o quadro e carregado uma vez por par
    e timeframe, com o maior aquecimento entre elas, e cada estrategia opina
    sobre ele. Assim os setups sao comparados sobre exatamente os mesmos dados,
    sem diferenca de janela mascarando diferenca de metodo.

    `minutos=0` roda indefinidamente, ate `parar()` devolver True - e o modo de
    servico. Cada vela e gravada assim que fecha, entao interromper a qualquer
    momento nao perde nada do que ja foi coletado.
    """
    indefinido = minutos <= 0
    fim = datetime.now(timezone.utc) + timedelta(minutes=max(minutos, 0))
    aquecimento = max(e.barras_de_aquecimento() for e in estrategias)
    resumo = ResumoColeta()

    def acabou() -> bool:
        if parar is not None and parar():
            return True
        return not indefinido and datetime.now(timezone.utc) >= fim

    while not acabou():
        resumo.ciclos += 1
        if ao_registrar and resumo.ciclos % ciclos_por_pulso == 0:
            # Pulso de vida: num servico que passa horas sem emitir sinal, o
            # log silencioso e indistinguivel de processo travado.
            ao_registrar(
                {
                    "pulso": (
                        f"ciclo {resumo.ciclos} | velas {resumo.velas_novas} | "
                        f"sinais {len(resumo.sinais)} | erros {resumo.erros}"
                    )
                }
            )
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

                    for estrategia in estrategias:
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
                        if not nova:
                            continue

                        resumo.velas_novas += 1
                        chave = (par, timeframe)
                        resumo.por_alvo[chave] = resumo.por_alvo.get(chave, 0) + 1
                        registro = {
                            "par": par,
                            "timeframe": timeframe,
                            "estrategia": estrategia.nome,
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

        if acabou():
            break
        restante = (
            float(intervalo_segundos)
            if indefinido
            else (fim - datetime.now(timezone.utc)).total_seconds()
        )
        if restante <= 0:
            break
        # Dorme em fatias de 1s para atender um pedido de parada na hora, em
        # vez de so no fim do intervalo - importante para `docker stop`, que
        # espera pouco antes de matar o processo a forca.
        dormir = min(intervalo_segundos, restante)
        while dormir > 0 and not (parar is not None and parar()):
            time.sleep(min(1.0, dormir))
            dormir -= 1

    return resumo


def conferir_paridade(
    pares: list[str],
    timeframes: list[str],
    estrategias: list[Estrategia],
    provedor,
    armazenamento: Armazenamento,
) -> pd.DataFrame:
    """Recalcula em lote e compara com o que foi decidido ao vivo.

    Ao vivo, cada decisao foi tomada com a serie que existia naquele instante.
    Aqui a serie inteira e recalculada de uma vez, como no backtest. Se algum
    valor mudar, o caminho ao vivo e o simulado nao sao o mesmo sistema.
    """
    linhas = []
    aquecimento = max(e.barras_de_aquecimento() for e in estrategias)

    for par in pares:
        for timeframe in timeframes:
            for estrategia in estrategias:
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
                        "estrategia": estrategia.nome[:28],
                        "velas_conferidas": len(comuns),
                        "divergencias": divergentes,
                        "situacao": "OK" if divergentes == 0 else "DIVERGE",
                    }
                )

    return pd.DataFrame(linhas)
