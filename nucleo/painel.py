"""Painel: a API por tras da tela.

Le o mesmo banco e chama as mesmas funcoes que a CLI. Nada aqui calcula por
conta propria: o painel mostra o que `campanha`, `decidir`, `rastrear` e
`analisar` diriam, e so. Se a tela e a linha de comando discordassem, uma das
duas estaria mentindo - por isso as duas chamam o mesmo codigo.

Nenhuma rota envia ordem e nenhuma rota escreve no banco alem do cache de
velas que o carregador ja mantinha.

Tudo que o painel consulta esta em `Fontes`, para o teste injetar dados de
mentira sem rede nem banco.
"""
from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from glob import glob
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import campanha as mod_campanha
from . import decisao, exportacao, rastreio, tempo
from .backtest import metricas
from .backtest.motor import MOTIVO_FIM, ConfigExecucao, ModeloCustos, executar
from .campanha import ConfigCampanha
from .dados.carregador import carregar
from .dados.provedor import duracao_ms
from .estrategias.base import Estrategia
from .risco.carteira import Carteira, RegrasCarteira

VERSAO = "0.1"


@dataclass
class Fontes:
    """Tudo que o painel consulta. Injetavel para teste."""

    provedor: object
    fabrica_armazenamento: Callable[[], object]
    setups: dict[str, Estrategia]
    descricoes: dict[str, str]
    pares: list[str]
    timeframes: list[str]
    campanha: ConfigCampanha
    regras: RegrasCarteira = field(default_factory=RegrasCarteira)
    custos: ModeloCustos = field(default_factory=ModeloCustos)
    execucao: ConfigExecucao = field(default_factory=ConfigExecucao)
    usar_rede: bool = True
    pasta_observacoes: Path | None = None
    pasta_campanha: Path | None = None
    pasta_painel: Path | None = None
    fuso: str | None = None
    # Nome completo -> apelido, para setups que aparecem nos dados coletados
    # mas nao estao na campanha (a confluencia, por exemplo).
    apelidos: dict[str, str] = field(default_factory=dict)


class Cache:
    """Guarda respostas caras por alguns segundos.

    O painel e uma pessoa olhando uma tela: recalcular a campanha a cada
    clique seria desperdicio, e baixar velas em paralelo para o mesmo par
    seria pedir conflito. Um cadeado serializa o trabalho pesado.
    """

    def __init__(self) -> None:
        self._guardado: dict[str, tuple[float, object]] = {}
        self.cadeado = threading.Lock()

    def lembrar(self, chave: str, ttl: float, calcular: Callable[[], object], atualizar: bool = False):
        with self.cadeado:
            agora = time.monotonic()
            if not atualizar and chave in self._guardado:
                validade, valor = self._guardado[chave]
                if agora < validade:
                    return valor
            # Limpa (NaN, numpy, Timestamp) uma vez so, antes de guardar.
            valor = _limpo(calcular())
            self._guardado[chave] = (time.monotonic() + ttl, valor)
            return valor


def _limpo(valor):
    """Deixa o objeto pronto para JSON: sem NaN, sem numpy, sem Timestamp."""
    if isinstance(valor, dict):
        return {str(k): _limpo(v) for k, v in valor.items()}
    if isinstance(valor, (list, tuple)):
        return [_limpo(v) for v in valor]
    if isinstance(valor, (np.integer,)):
        return int(valor)
    if isinstance(valor, (np.floating, float)):
        f = float(valor)
        return None if math.isnan(f) or math.isinf(f) else f
    if isinstance(valor, (np.bool_,)):
        return bool(valor)
    if isinstance(valor, (pd.Timestamp, datetime)):
        return tempo.local(valor).isoformat(timespec="minutes")
    if isinstance(valor, pd.Series):
        return _limpo(valor.tolist())
    if isinstance(valor, pd.DataFrame):
        return _limpo(valor.to_dict(orient="records"))
    return valor


def _epoch_local(momento: pd.Timestamp, z) -> int:
    """Segundos de epoch deslocados para o fuso: e assim que o grafico mostra hora local."""
    local = tempo.local(momento, z)
    return int(momento.timestamp() + local.utcoffset().total_seconds())


class Servico:
    """As respostas do painel, sem HTTP - o que os testes e as rotas chamam."""

    def __init__(self, fontes: Fontes) -> None:
        self.f = fontes
        self.cache = Cache()
        self.z = tempo.fuso(fontes.fuso)

    # ------------------------------------------------------------ apoio

    def _armazenamento(self):
        return self.f.fabrica_armazenamento()

    def _estrategias(self) -> list[Estrategia]:
        return list(self.f.setups.values())

    def _nome_curto(self, nome_completo: str) -> str:
        for curto, e in self.f.setups.items():
            if e.nome == nome_completo:
                return curto
        return self.f.apelidos.get(nome_completo, nome_completo)

    # ------------------------------------------------------------ estado

    def estado(self) -> dict:
        def calcular():
            agora = datetime.now(timezone.utc)
            c = self.f.campanha
            armazenamento = self._armazenamento()
            velas = []
            try:
                for par in self.f.pares:
                    for tf in self.f.timeframes:
                        # Velas suficientes para a variacao de 24h no timeframe.
                        em_24h = max(1, int(24 * 3600 * 1000 // duracao_ms(tf)))
                        try:
                            quadro = carregar(
                                par, tf, agora - timedelta(milliseconds=duracao_ms(tf) * (em_24h + 6)),
                                provedor=self.f.provedor, armazenamento=armazenamento,
                                usar_rede=False,
                            )
                        except Exception:
                            quadro = pd.DataFrame()
                        fechamentos = quadro["fechamento"] if not quadro.empty else pd.Series(dtype=float)
                        variacao = (
                            float(fechamentos.iloc[-1] / fechamentos.iloc[-1 - em_24h] - 1)
                            if len(fechamentos) > em_24h else None
                        )
                        velas.append(
                            {
                                "par": par, "timeframe": tf,
                                "ultima": None if quadro.empty else quadro.index[-1],
                                "fechamento": None if quadro.empty else float(fechamentos.iloc[-1]),
                                "variacao_24h": variacao,
                                "total": armazenamento.contar_velas(
                                    getattr(self.f.provedor, "nome", ""), par, tf
                                ),
                            }
                        )
            finally:
                armazenamento.fechar()

            nuvem = None
            if self.f.pasta_campanha and (self.f.pasta_campanha / "relatorio.json").exists():
                try:
                    import json

                    dados = json.loads(
                        (self.f.pasta_campanha / "relatorio.json").read_text(encoding="utf-8")
                    )
                    nuvem = {"gerado_em": dados.get("gerado_em"), "ultima_vela": dados.get("ultima_vela")}
                except Exception:
                    nuvem = None

            # "ate sexta" inclui a sexta: o ultimo dia e um segundo antes do fim.
            fim_inclusivo = c.fim - timedelta(seconds=1)
            dias_restantes = max(
                0, (tempo.local(fim_inclusivo, self.z).date() - tempo.local(agora, self.z).date()).days
            )
            return {
                "versao": VERSAO,
                "agora": agora,
                "fuso": self.z.key,
                "rotulo_fuso": tempo.rotulo(self.z),
                "corretora": getattr(self.f.provedor, "nome", ""),
                "rede": self.f.usar_rede,
                "pares": self.f.pares,
                "timeframes": self.f.timeframes,
                "setups": [
                    {"nome": curto, "completo": e.nome, "descricao": self.f.descricoes.get(curto, "")}
                    for curto, e in self.f.setups.items()
                ],
                "campanha": {
                    "inicio": c.inicio, "fim": fim_inclusivo, "banca": c.banca, "moeda": c.moeda,
                    "timeframes": list(c.timeframes), "dias_restantes": dias_restantes,
                    "encerrada": agora >= c.fim,
                },
                "velas": velas,
                "nuvem": nuvem,
            }

        return self.cache.lembrar("estado", 30, calcular)

    # ------------------------------------------------------------ campanha

    def campanha(self, atualizar: bool = False) -> dict:
        def calcular():
            armazenamento = self._armazenamento()
            try:
                resultado = mod_campanha.rodar(
                    self.f.campanha, self._estrategias(), self.f.provedor, armazenamento,
                    usar_rede=self.f.usar_rede,
                )
            finally:
                armazenamento.fechar()

            dados = mod_campanha.para_json(resultado, self.z.key)
            ranking = resultado.ranking()
            dados["ranking"] = [] if ranking.empty else ranking.replace([np.inf, -np.inf], np.nan).to_dict(orient="records")
            for linha in dados["ranking"]:
                linha["nome_curto"] = self._nome_curto(linha["trader"])
                linha["descricao"] = self.f.descricoes.get(linha["nome_curto"], "")
            curvas = {}
            for t in resultado.traders:
                pontos = [{"t": self.f.campanha.inicio, "saldo": self.f.campanha.banca}]
                pontos += [{"t": momento, "saldo": float(saldo)} for momento, saldo in t.carteira.curva.items()]
                curvas[t.nome] = pontos
            dados["curvas"] = curvas
            dados["fim_inclusivo"] = self.f.campanha.fim - timedelta(seconds=1)
            dados["relatorio"] = mod_campanha.relatorio_simples(resultado, self.z.key)
            maximo = int(ranking["operacoes"].max()) if not ranking.empty else 0
            dados["cedo"] = maximo < mod_campanha.MINIMO_PARA_OPINAR
            dados["minimo_para_opinar"] = mod_campanha.MINIMO_PARA_OPINAR
            dados["maximo_operacoes"] = maximo
            dados["velas_no_periodo"] = resultado.velas_no_periodo
            return dados

        return self.cache.lembrar("campanha", 120, calcular, atualizar)

    # ------------------------------------------------------------ decisao

    def decisao(self, banca: float | None = None, moeda: str | None = None, atualizar: bool = False) -> dict:
        banca = banca or self.f.regras.saldo_inicial
        moeda = moeda or self.f.regras.moeda

        def calcular():
            regras = RegrasCarteira(**{**self.f.regras.__dict__, "saldo_inicial": banca, "moeda": moeda})
            armazenamento = self._armazenamento()
            try:
                recomendacoes = decisao.varrer(
                    self.f.pares, self.f.timeframes, self._estrategias(),
                    self.f.provedor, armazenamento, Carteira(regras), usar_rede=self.f.usar_rede,
                )
            finally:
                armazenamento.fechar()
            saida = []
            for r in recomendacoes:
                d = decisao.para_json([r])[0]
                risco = abs(r.preco - r.stop)
                d["nome_curto"] = self._nome_curto(r.estrategia)
                d["stop_pct"] = (r.stop / r.preco - 1) if r.preco else None
                d["alvo_pct"] = (r.alvo / r.preco - 1) if r.preco else None
                d["razao_risco_retorno"] = (abs(r.alvo - r.preco) / risco) if risco else None
                saida.append(d)
            return {
                "gerado_em": datetime.now(timezone.utc),
                "banca": banca, "moeda": moeda,
                "regras": {
                    "risco_por_trade": regras.risco_por_trade, "max_posicoes": regras.max_posicoes,
                    "max_por_par": regras.max_por_par, "exposicao_maxima": regras.exposicao_maxima,
                    "perda_diaria_maxima": regras.perda_diaria_maxima,
                },
                "recomendacoes": saida,
                "entrar": sum(1 for r in recomendacoes if r.decisao == decisao.ENTRAR),
            }

        return self.cache.lembrar(f"decisao:{banca}:{moeda}", 60, calcular, atualizar)

    # ------------------------------------------------------------ velas

    def _setup_no_grafico(self, quadro, corte, curto: str, estrategia: Estrategia, com_linhas: bool) -> dict:
        """Sinais, linhas e operacoes de um setup sobre o quadro ja carregado."""
        z = self.z
        sinais = estrategia.gerar_sinais(quadro)

        # Linhas que vivem na escala do preco (medias, canais, bandas, VWAP,
        # topos/fundos) vao sobre as velas; osciladores ficam de fora.
        linhas = {}
        if com_linhas:
            painel = estrategia.painel_indicadores(quadro)
            mediana = float(corte["fechamento"].median())
            for coluna in painel.columns:
                serie = painel[coluna]
                if serie.dtype == bool or not pd.api.types.is_numeric_dtype(serie):
                    continue
                recorte = serie.reindex(corte.index)
                centro = recorte.median()
                if pd.isna(centro) or not (0.5 * mediana <= centro <= 2 * mediana):
                    continue
                linhas[coluna] = [
                    {"t": _epoch_local(momento, z), "v": float(valor)}
                    for momento, valor in recorte.items() if pd.notna(valor)
                ]

        recorte_sinais = sinais.reindex(corte.index)
        ativos = recorte_sinais[recorte_sinais["direcao"] != 0]
        marcas = [
            {
                "t": _epoch_local(momento, z), "direcao": int(s.direcao), "forca": float(s.forca),
                "stop": None if pd.isna(s.stop) else float(s.stop),
                "alvo": None if pd.isna(s.alvo) else float(s.alvo),
                "motivo": str(s.motivo), "preco": float(corte.loc[momento, "fechamento"]),
                "quando": momento,
            }
            for momento, s in ativos.iterrows()
        ]

        # O mesmo motor do backtest diz o que cada sinal virou: onde entrou
        # (abertura da vela seguinte), onde saiu e por que. E a resposta
        # honesta para "esse sinal deu certo?".
        trades = executar(quadro, sinais, self.f.custos, self.f.execucao).trades

        def operacao(tr) -> dict:
            aberto = tr.motivo_saida == MOTIVO_FIM
            return {
                "setup": curto,
                "direcao": int(tr.direcao),
                "entrada": tr.entrada, "t_entrada": _epoch_local(tr.entrada, z),
                "preco_entrada": float(tr.preco_entrada),
                "saida": tr.saida, "t_saida": _epoch_local(tr.saida, z),
                "preco_saida": float(tr.preco_saida),
                "motivo_saida": str(tr.motivo_saida), "aberto": aberto,
                "retorno_liquido_pct": float(tr.retorno_liquido_pct),
                "multiplo_r": float(tr.multiplo_r),
                "stop": float(tr.stop), "alvo": float(tr.alvo),
                "barras": int(tr.barras_no_trade),
            }

        operacoes = (
            [operacao(tr) for tr in trades.itertuples() if tr.saida >= corte.index[0]]
            if not trades.empty else []
        )

        ultimo_trade, motivo_sem_trade = None, None
        if not ativos.empty:
            posicao = quadro.index.get_loc(ativos.index[-1])
            if posicao + 1 >= len(quadro):
                motivo_sem_trade = "a entrada seria na abertura da proxima vela, que ainda nao fechou"
            else:
                esperada = quadro.index[posicao + 1]
                casado = trades[trades["entrada"] == esperada] if not trades.empty else trades
                if casado.empty:
                    motivo_sem_trade = "ja havia posicao aberta neste par quando o sinal saiu"
                else:
                    ultimo_trade = operacao(next(casado.itertuples()))

        return {
            "nome": curto, "completo": estrategia.nome, "linhas": linhas, "sinais": marcas,
            "operacoes": operacoes, "ultimo_trade": ultimo_trade, "motivo_sem_trade": motivo_sem_trade,
        }

    def velas(self, par: str, timeframe: str, setup: str, barras: int = 300) -> dict:
        if par not in self.f.pares:
            raise HTTPException(404, f"par desconhecido: {par}")
        todos = setup == "todos"
        if not todos and setup not in self.f.setups:
            raise HTTPException(404, f"setup desconhecido: {setup}")
        estrategias = list(self.f.setups.items()) if todos else [(setup, self.f.setups[setup])]
        barras = max(50, min(barras, 2000))

        def calcular():
            passo = duracao_ms(timeframe)
            aquecimento = max(e.barras_de_aquecimento() for _, e in estrategias)
            inicio = datetime.now(timezone.utc) - timedelta(milliseconds=passo * (aquecimento + barras + 5))
            armazenamento = self._armazenamento()
            try:
                quadro = carregar(
                    par, timeframe, inicio, provedor=self.f.provedor, armazenamento=armazenamento,
                    usar_rede=self.f.usar_rede,
                )
            finally:
                armazenamento.fechar()
            if quadro.empty:
                raise HTTPException(404, f"sem velas para {par} {timeframe}")

            corte = quadro.tail(barras)
            z = self.z
            velas = [
                {
                    "t": _epoch_local(momento, z),
                    "o": float(v.abertura), "h": float(v.maxima), "l": float(v.minima),
                    "c": float(v.fechamento), "v": float(v.volume),
                }
                for momento, v in corte.iterrows()
            ]
            por_setup = [
                self._setup_no_grafico(quadro, corte, curto, e, com_linhas=not todos)
                for curto, e in estrategias
            ]
            base = {
                "par": par, "timeframe": timeframe, "fuso": z.key, "rotulo_fuso": tempo.rotulo(z),
                "ultima_vela": corte.index[-1], "preco": float(corte["fechamento"].iloc[-1]),
                "velas": velas,
            }
            if todos:
                # No modo "todos" nao ha linhas de indicador nem stop/alvo: oito
                # setups sobrepostos virariam ruido. Ficam as entradas e saidas.
                base.update(
                    {
                        "setup": "todos", "setup_completo": "todos os setups",
                        "linhas": {}, "sinais": [], "ultimo_sinal": None, "ultimo_trade": None,
                        "motivo_sem_trade": None, "operacoes": [], "por_setup": por_setup,
                    }
                )
            else:
                unico = por_setup[0]
                base.update(
                    {
                        "setup": setup, "setup_completo": unico["completo"],
                        "linhas": unico["linhas"], "sinais": unico["sinais"],
                        "ultimo_sinal": unico["sinais"][-1] if unico["sinais"] else None,
                        "ultimo_trade": unico["ultimo_trade"], "motivo_sem_trade": unico["motivo_sem_trade"],
                        "operacoes": unico["operacoes"], "por_setup": None,
                    }
                )
            return base

        return self.cache.lembrar(f"velas:{par}:{timeframe}:{setup}:{barras}", 60, calcular)

    # ------------------------------------------------------------ rastreio

    def rastreio(self, atualizar: bool = False) -> dict:
        def calcular():
            partes = []
            if self.f.pasta_observacoes:
                caminhos = sorted(glob(str(self.f.pasta_observacoes / "*.jsonl")))
                if caminhos:
                    partes.append(exportacao.ler(caminhos))
            armazenamento = self._armazenamento()
            try:
                do_banco = armazenamento.observacoes()
                if not do_banco.empty:
                    do_banco["vela"] = pd.to_datetime(do_banco["vela_ms"], unit="ms", utc=True)
                    partes.append(do_banco)
                observacoes = pd.concat(partes, ignore_index=True) if partes else pd.DataFrame()
                if observacoes.empty:
                    return {"fontes": len(partes), "observacoes": 0, "sinais": 0, "setups": [], "trades": [], "veredito": ""}
                observacoes = observacoes.drop_duplicates(subset=["par", "timeframe", "estrategia", "vela"])
                sinais = rastreio.preparar_observacoes(observacoes)
                trades = rastreio.rastrear(
                    sinais, self.f.provedor, armazenamento, self.f.custos, self.f.execucao,
                    usar_rede=self.f.usar_rede,
                ) if not sinais.empty else pd.DataFrame()
            finally:
                armazenamento.fechar()

            setups = []
            if not trades.empty:
                for nome, grupo in trades.groupby("estrategia", sort=True):
                    fechados = grupo[~grupo["aberto"]]
                    retornos = fechados["retorno_liquido_pct"]
                    ganhos, perdas = retornos[retornos > 0], retornos[retornos <= 0]
                    baixo, alto = metricas.intervalo_wilson(len(ganhos), len(fechados))
                    payoff = (ganhos.mean() / abs(perdas.mean())) if len(ganhos) and len(perdas) else None
                    setups.append(
                        {
                            "estrategia": nome, "nome_curto": self._nome_curto(nome),
                            "fechados": int(len(fechados)), "abertos": int(grupo["aberto"].sum()),
                            "ganhos": int(len(ganhos)), "perdidos": int(len(perdas)),
                            "acerto": (len(ganhos) / len(fechados)) if len(fechados) else None,
                            "acerto_de": baixo, "acerto_ate": alto,
                            "payoff": payoff,
                            "acerto_para_empatar": (1 / (1 + payoff)) if payoff else None,
                            "expectancia_r": float(fechados["multiplo_r"].mean()) if len(fechados) else None,
                            "retorno_medio": float(retornos.mean()) if len(fechados) else None,
                            "amostra_ok": len(fechados) >= metricas.MINIMO_PARA_CONCLUIR,
                        }
                    )
                setups.sort(key=lambda s: (-(s["expectancia_r"] or -9), s["estrategia"]))

            colunas = [c for c in (
                "estrategia", "par", "timeframe", "direcao", "entrada", "saida", "preco_entrada",
                "preco_saida", "stop", "alvo", "retorno_liquido_pct", "multiplo_r", "motivo_saida",
                "barras_no_trade", "aberto",
            ) if not trades.empty and c in trades.columns]
            recentes = trades.sort_values("entrada", ascending=False).head(60)[colunas] if colunas else pd.DataFrame()
            if not recentes.empty:
                recentes = recentes.assign(nome_curto=recentes["estrategia"].map(self._nome_curto))

            return {
                "gerado_em": datetime.now(timezone.utc),
                "fontes": len(partes),
                "observacoes": int(len(observacoes)),
                "sinais": int(len(sinais)),
                "fechados": int((~trades["aberto"]).sum()) if not trades.empty else 0,
                "abertos": int(trades["aberto"].sum()) if not trades.empty else 0,
                "minimo_para_concluir": metricas.MINIMO_PARA_CONCLUIR,
                "setups": setups,
                "trades": recentes,
                "veredito": rastreio.texto_do_veredito(trades) if not trades.empty else "",
            }

        return self.cache.lembrar("rastreio", 180, calcular, atualizar)


def criar_app(fontes: Fontes) -> FastAPI:
    servico = Servico(fontes)
    app = FastAPI(title="Painel de analise", version=VERSAO, docs_url="/api/docs", redoc_url=None)
    app.state.servico = servico

    @app.get("/api/estado")
    def estado():
        return servico.estado()

    @app.get("/api/campanha")
    def campanha(atualizar: bool = False):
        return servico.campanha(atualizar=atualizar)

    @app.get("/api/decisao")
    def decisao_(banca: float | None = None, moeda: str | None = None, atualizar: bool = False):
        return servico.decisao(banca, moeda, atualizar=atualizar)

    @app.get("/api/velas")
    def velas(
        par: str = Query(...), tf: str = Query(...), setup: str = Query(...),
        barras: int = Query(300, ge=50, le=2000),
    ):
        return servico.velas(par, tf, setup, barras)

    @app.get("/api/rastreio")
    def rastreio_(atualizar: bool = False):
        return servico.rastreio(atualizar=atualizar)

    pasta = fontes.pasta_painel
    if pasta and (pasta / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=str(pasta / "assets")), name="assets")

        @app.get("/{caminho:path}", include_in_schema=False)
        def tela(caminho: str):
            alvo = pasta / caminho
            if caminho and alvo.is_file():
                return FileResponse(str(alvo))
            return FileResponse(str(pasta / "index.html"))

    return app
