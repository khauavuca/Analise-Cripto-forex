"""Servico de decisao: varre o mercado e diz o que faria, com quanto, e por que nao.

E o lugar que uma IA de apoio ocuparia. Hoje quem decide sao os setups e as
regras da carteira; o filtro de ML, quando existe e foi aprovado, so veta. A
saida e estruturada de proposito - tabela para gente, JSON para maquina -, para
que trocar quem decide nao mude quem consome.

Nada aqui envia ordem. A recomendacao e um registro do que o sistema faria;
executar e decisao de quem opera.
"""
from __future__ import annotations

import copy
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from .aprendizado.conjunto import (
    caracteristicas_no_instante,
    contexto_de_mercado,
    normalizar_painel,
)
from .dados.carregador import carregar
from .dados.provedor import duracao_ms
from .estrategias.base import Estrategia
from .risco.carteira import Carteira

ENTRAR = "ENTRAR"
RECUSADA = "RECUSADA"


def nome_de_arquivo(nome_do_setup: str) -> str:
    """Nome do setup convertido para algo que o sistema de arquivos aceita.

    `ema(21/55,evento)` tem uma barra, que viraria subpasta. A mesma regra e
    usada para gravar o modelo e para procura-lo depois - se divergissem, o
    `decidir` nunca encontraria o filtro que o `filtro` acabou de salvar.
    """
    trocas = {"/": "-", "\\": "-", ":": "-", " ": "_", "*": "x", "?": "", '"': "", "<": "", ">": "", "|": "-"}
    return "".join(trocas.get(c, c) for c in nome_do_setup)


@dataclass
class Recomendacao:
    vela: str
    par: str
    timeframe: str
    estrategia: str
    direcao: int
    forca: float
    preco: float
    stop: float
    alvo: float
    motivo: str
    decisao: str
    motivo_recusa: str = ""
    valor_ordem: float = 0.0
    risco_moeda: float = 0.0
    probabilidade: float | None = None

    @property
    def lado(self) -> str:
        return "COMPRA" if self.direcao > 0 else "VENDA"


def _candidatos(quadro: pd.DataFrame, estrategia: Estrategia, par: str, timeframe: str,
                filtro=None) -> list[Recomendacao]:
    sinais = estrategia.gerar_sinais(quadro)
    ultimo = sinais.iloc[-1]
    if int(ultimo.direcao) == 0 or pd.isna(ultimo.stop) or pd.isna(ultimo.alvo):
        return []

    momento = quadro.index[-1]
    preco = float(quadro["fechamento"].iloc[-1])
    rec = Recomendacao(
        vela=momento.strftime("%Y-%m-%d %H:%M"), par=par, timeframe=timeframe,
        estrategia=estrategia.nome, direcao=int(ultimo.direcao), forca=float(ultimo.forca),
        preco=preco, stop=float(ultimo.stop), alvo=float(ultimo.alvo), motivo=str(ultimo.motivo),
        decisao=ENTRAR,
    )

    if filtro is not None and filtro.treinado:
        painel = normalizar_painel(estrategia.painel_indicadores(quadro), quadro["fechamento"])
        contexto = contexto_de_mercado(quadro)
        x = caracteristicas_no_instante(
            painel, contexto, preco, len(quadro) - 1,
            rec.direcao, rec.forca, rec.stop, rec.alvo,
        )
        rec.probabilidade = float(filtro.probabilidade(pd.DataFrame([x]))[0])
        if rec.probabilidade < filtro.config.limiar:
            rec.decisao = RECUSADA
            rec.motivo_recusa = f"filtro ml ({rec.probabilidade:.0%} < {filtro.config.limiar:.0%})"
    return [rec]


def varrer(
    pares: list[str],
    timeframes: list[str],
    estrategias: list[Estrategia],
    provedor,
    armazenamento,
    carteira: Carteira,
    filtros: dict[str, object] | None = None,
    usar_rede: bool = True,
) -> list[Recomendacao]:
    """Uma recomendacao por sinal ativo, ja passada pelas regras da carteira.

    Os candidatos sao avaliados em ordem de forca, numa COPIA da carteira, para
    que o segundo sinal veja o primeiro ja posicionado - se cinco disparam e o
    teto e tres, entram os tres mais fortes e os outros ficam com o motivo.
    """
    filtros = filtros or {}
    aquecimento = max(e.barras_de_aquecimento() for e in estrategias)
    candidatos: list[Recomendacao] = []

    for par in pares:
        for timeframe in timeframes:
            quadro = carregar(
                par, timeframe,
                datetime.now(timezone.utc)
                - timedelta(milliseconds=duracao_ms(timeframe) * (aquecimento + 60)),
                provedor=provedor, armazenamento=armazenamento,
                barras_aquecimento=aquecimento, usar_rede=usar_rede,
            )
            if quadro.empty:
                continue
            for estrategia in estrategias:
                candidatos.extend(
                    _candidatos(quadro, estrategia, par, timeframe, filtros.get(estrategia.nome))
                )

    candidatos.sort(key=lambda r: (r.decisao != ENTRAR, -r.forca))
    simulada = copy.deepcopy(carteira)
    agora = pd.Timestamp.now(tz="UTC")

    for rec in candidatos:
        if rec.decisao != ENTRAR:
            continue
        posicao = simulada.abrir(
            chave=(rec.par, rec.estrategia, rec.vela), par=rec.par, estrategia=rec.estrategia,
            direcao=rec.direcao, momento=agora, preco_entrada=rec.preco, stop=rec.stop,
        )
        if posicao is None:
            rec.decisao = RECUSADA
            rec.motivo_recusa = simulada.recusas[-1].motivo
        else:
            rec.valor_ordem = posicao.valor
            rec.risco_moeda = posicao.risco

    candidatos.sort(key=lambda r: (r.decisao != ENTRAR, -r.forca))
    return candidatos


def tabela(recomendacoes: list[Recomendacao]) -> pd.DataFrame:
    if not recomendacoes:
        return pd.DataFrame()
    linhas = []
    for r in recomendacoes:
        linhas.append(
            {
                "vela": r.vela, "par": r.par, "tf": r.timeframe, "setup": r.estrategia[:22],
                "lado": r.lado, "forca": f"{r.forca:.0%}",
                "prob": "" if r.probabilidade is None else f"{r.probabilidade:.0%}",
                "preco": round(r.preco, 4), "stop": round(r.stop, 4), "alvo": round(r.alvo, 4),
                "ordem": round(r.valor_ordem, 2) if r.decisao == ENTRAR else "",
                "risco": round(r.risco_moeda, 2) if r.decisao == ENTRAR else "",
                "decisao": r.decisao if r.decisao == ENTRAR else f"{RECUSADA}: {r.motivo_recusa}",
            }
        )
    return pd.DataFrame(linhas)


def para_json(recomendacoes: list[Recomendacao]) -> list[dict]:
    saida = []
    for r in recomendacoes:
        d = asdict(r)
        d["lado"] = r.lado
        saida.append(d)
    return saida
