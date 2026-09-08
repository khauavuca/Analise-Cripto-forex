"""Painel: as respostas da tela vem do mesmo codigo da CLI, sem rede e sem NaN."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from nucleo.campanha import ConfigCampanha
from nucleo.dados.armazenamento import Armazenamento
from nucleo.estrategias.base import COMPRA, Estrategia, quadro_sinais
from nucleo.risco.carteira import RegrasCarteira

painel = pytest.importorskip("nucleo.painel", reason="fastapi nao instalado")


class SempreCompra(Estrategia):
    """Compra a cada N barras com stop e alvo fixos; expoe uma media como painel."""

    def __init__(self, nome: str, a_cada: int, stop: float = 0.05, alvo: float = 0.02):
        self.nome = nome
        self.a_cada = a_cada
        self.stop_pct = stop
        self.alvo_pct = alvo

    def barras_de_aquecimento(self) -> int:
        return 5

    def painel_indicadores(self, quadro: pd.DataFrame) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "media": quadro["fechamento"].rolling(5).mean(),   # escala de preco: vira linha
                "oscilador": quadro["fechamento"].pct_change(),   # nao e preco: fica de fora
                "ligado": quadro["fechamento"] > 0,               # booleano: fica de fora
            },
            index=quadro.index,
        )

    def gerar_sinais(self, quadro: pd.DataFrame) -> pd.DataFrame:
        sinais = quadro_sinais(quadro.index)
        alvo = (np.arange(len(quadro)) % self.a_cada) == (len(quadro) - 1) % self.a_cada
        fechamento = quadro["fechamento"]
        sinais.loc[alvo, "direcao"] = np.int8(COMPRA)
        sinais.loc[alvo, "forca"] = 0.7
        sinais.loc[alvo, "stop"] = (fechamento * (1 - self.stop_pct))[alvo]
        sinais.loc[alvo, "alvo"] = (fechamento * (1 + self.alvo_pct))[alvo]
        return sinais


class ProvedorFalso:
    nome = "falso"

    def timeframes_nativos(self):
        return frozenset({"1h", "4h"})

    def obter_velas(self, *args, **kwargs):
        raise AssertionError("o painel offline nao pode ir a rede")


def mercado_recente(barras: int, timeframe: str) -> pd.DataFrame:
    passo = pd.Timedelta(timeframe)
    fim = pd.Timestamp.now(tz="UTC").floor(timeframe) - passo
    fechamento = 100 + 0.4 * np.arange(barras)
    anterior = np.r_[fechamento[0], fechamento[:-1]]
    return pd.DataFrame(
        {
            "abertura": anterior,
            "maxima": np.maximum(fechamento, anterior) + 0.2,
            "minima": np.minimum(fechamento, anterior) - 0.2,
            "fechamento": fechamento,
            "volume": np.ones(barras),
        },
        index=pd.date_range(end=fim, periods=barras, freq=timeframe, tz="UTC"),
    )


@pytest.fixture
def fontes(tmp_path):
    caminho = str(tmp_path / "painel.db")
    armazenamento = Armazenamento(caminho)
    for tf, barras in (("1h", 400), ("4h", 200)):
        armazenamento.salvar_velas("falso", "X/USDT", tf, mercado_recente(barras, tf))
    armazenamento.fechar()

    agora = datetime.now(timezone.utc)
    return painel.Fontes(
        provedor=ProvedorFalso(),
        fabrica_armazenamento=lambda: Armazenamento(caminho),
        setups={"sempre": SempreCompra("sempre(1)", a_cada=1), "raro": SempreCompra("raro(40)", a_cada=40)},
        descricoes={"sempre": "compra toda vela", "raro": "compra de vez em quando"},
        pares=["X/USDT"],
        timeframes=["1h", "4h"],
        campanha=ConfigCampanha(
            inicio=agora - timedelta(days=4), fim=agora + timedelta(days=3), banca=500.0, moeda="BRL",
            pares=("X/USDT",), timeframes=("1h",),
            regras=RegrasCarteira(max_posicoes=9, max_por_par=9, exposicao_maxima=9,
                                  perda_diaria_maxima=0, perdas_seguidas_para_pausa=99),
        ),
        regras=RegrasCarteira(saldo_inicial=500.0, moeda="BRL", max_posicoes=9, max_por_par=9, exposicao_maxima=9),
        usar_rede=False,
        fuso="America/Sao_Paulo",
    )


def json_estrito(dados) -> str:
    """NaN ou infinito aqui quebraria o navegador; o json.dumps estrito pega."""
    return json.dumps(dados, allow_nan=False)


class TestEstado:
    def test_descreve_o_sistema_sem_ir_a_rede(self, fontes):
        e = painel.Servico(fontes).estado()
        json_estrito(e)
        assert e["fuso"] == "America/Sao_Paulo" and e["rotulo_fuso"] == "horario de Brasilia"
        assert [s["nome"] for s in e["setups"]] == ["sempre", "raro"]
        assert e["campanha"]["banca"] == 500.0 and e["campanha"]["dias_restantes"] >= 2
        velas = {(v["par"], v["timeframe"]): v for v in e["velas"]}
        assert velas[("X/USDT", "1h")]["total"] == 400
        assert velas[("X/USDT", "1h")]["ultima"].endswith("-03:00")
        # Mercado sobe 0,4 por vela: em 24 velas de 1h a variacao e positiva e pequena.
        assert 0 < velas[("X/USDT", "1h")]["variacao_24h"] < 0.1
        assert 0 < velas[("X/USDT", "4h")]["variacao_24h"] < 0.1


class TestCampanha:
    def test_ranking_curvas_e_relatorio(self, fontes):
        c = painel.Servico(fontes).campanha()
        json_estrito(c)
        nomes = [r["nome_curto"] for r in c["ranking"]]
        assert set(nomes) == {"sempre", "raro"}
        lider = c["ranking"][0]
        assert lider["banca_atual"] >= c["ranking"][1]["banca_atual"]
        assert lider["descricao"] in ("compra toda vela", "compra de vez em quando")
        assert c["curvas"]["sempre(1)"][0]["saldo"] == 500.0
        assert len(c["curvas"]["sempre(1)"]) > 1
        assert "CAMPANHA DE TESTE" in c["relatorio"]
        assert c["minimo_para_opinar"] == 30 and isinstance(c["cedo"], bool)

    def test_cache_evita_recalcular(self, fontes):
        s = painel.Servico(fontes)
        a = s.campanha()
        b = s.campanha()
        assert a is b
        assert s.campanha(atualizar=True) is not a


class TestDecisao:
    def test_sinal_na_ultima_vela_vira_recomendacao_dimensionada(self, fontes):
        d = painel.Servico(fontes).decisao(banca=1000.0, moeda="BRL")
        json_estrito(d)
        assert d["banca"] == 1000.0
        recomendacoes = [r for r in d["recomendacoes"] if r["nome_curto"] == "sempre"]
        assert recomendacoes, "o setup que compra toda vela tem sinal na ultima vela"
        r = recomendacoes[0]
        assert r["decisao"] == "ENTRAR" and r["valor_ordem"] > 0 and r["risco_moeda"] > 0
        assert r["stop_pct"] == pytest.approx(-0.05, abs=0.001)
        assert r["razao_risco_retorno"] == pytest.approx(0.4, abs=0.01)


class TestVelas:
    def test_velas_linhas_e_sinais_no_fuso_de_quem_le(self, fontes):
        v = painel.Servico(fontes).velas("X/USDT", "1h", "sempre", barras=100)
        json_estrito(v)
        assert len(v["velas"]) == 100
        assert list(v["linhas"]) == ["media"], "so a linha na escala do preco vira overlay"
        assert v["sinais"] and v["ultimo_sinal"]["direcao"] == 1
        assert v["ultimo_sinal"]["stop"] < v["ultimo_sinal"]["preco"] < v["ultimo_sinal"]["alvo"]
        # O epoch vem deslocado 3h para o grafico mostrar hora de Brasilia.
        ultima = pd.Timestamp(v["ultima_vela"])
        assert v["velas"][-1]["t"] == int(ultima.timestamp()) - 3 * 3600

    def test_sinal_na_ultima_vela_ainda_nao_virou_operacao(self, fontes):
        # O setup "sempre" da sinal na ultima vela fechada: a entrada seria na
        # abertura da proxima, que nao existe ainda. O painel diz isso em vez
        # de inventar um trade.
        v = painel.Servico(fontes).velas("X/USDT", "1h", "sempre", barras=100)
        assert v["ultimo_trade"] is None
        assert "proxima vela" in v["motivo_sem_trade"]
        assert v["operacoes"], "os sinais anteriores viraram operacoes"
        op = v["operacoes"][-1]
        assert op["preco_entrada"] > 0 and op["motivo_saida"] in ("ALVO", "STOP", "TEMPO", "FIM_DADOS")

    def test_ultimo_sinal_casa_com_o_trade_que_gerou(self, fontes):
        class UmSinal(SempreCompra):
            """Um unico sinal, cinco velas antes do fim."""

            def gerar_sinais(self, quadro):
                sinais = quadro_sinais(quadro.index)
                i = len(quadro) - 6
                fechamento = float(quadro["fechamento"].iloc[i])
                sinais.iloc[i, sinais.columns.get_loc("direcao")] = np.int8(COMPRA)
                sinais.iloc[i, sinais.columns.get_loc("forca")] = 0.5
                sinais.iloc[i, sinais.columns.get_loc("stop")] = fechamento * 0.95
                sinais.iloc[i, sinais.columns.get_loc("alvo")] = fechamento * 1.5
                return sinais

        fontes.setups["um"] = UmSinal("um(1)", a_cada=1)
        v = painel.Servico(fontes).velas("X/USDT", "1h", "um", barras=100)
        json_estrito(v)
        op = v["ultimo_trade"]
        assert op is not None and v["motivo_sem_trade"] is None
        # Entrou na vela seguinte ao sinal, e o alvo de +50% nao chegou: aberta.
        assert pd.Timestamp(op["entrada"]) == pd.Timestamp(v["ultimo_sinal"]["quando"]) + pd.Timedelta("1h")
        assert op["aberto"] is True and op["motivo_saida"] == "FIM_DADOS"
        assert op["t_entrada"] == v["ultimo_sinal"]["t"] + 3600

    def test_todos_os_setups_no_mesmo_grafico(self, fontes):
        v = painel.Servico(fontes).velas("X/USDT", "1h", "todos", barras=100)
        json_estrito(v)
        assert v["setup"] == "todos" and v["linhas"] == {} and v["sinais"] == []
        assert [s["nome"] for s in v["por_setup"]] == ["sempre", "raro"]
        sempre = v["por_setup"][0]
        assert sempre["operacoes"] and all(o["setup"] == "sempre" for o in sempre["operacoes"])
        assert len(sempre["sinais"]) == 100, "um sinal por vela mostrada"
        assert "proxima vela" in sempre["motivo_sem_trade"]

    def test_par_ou_setup_desconhecido_da_404(self, fontes):
        from fastapi import HTTPException

        s = painel.Servico(fontes)
        with pytest.raises(HTTPException):
            s.velas("Y/USDT", "1h", "sempre")
        with pytest.raises(HTTPException):
            s.velas("X/USDT", "1h", "inexistente")


class TestRastreio:
    def test_sem_observacoes_responde_vazio(self, fontes):
        r = painel.Servico(fontes).rastreio()
        json_estrito(r)
        assert r["observacoes"] == 0 and r["setups"] == [] and r["trades"] == []


class TestApp:
    def test_rotas_existem_e_nenhuma_escreve(self, fontes):
        app = painel.criar_app(fontes)
        rotas = {(r.path, tuple(sorted(r.methods))) for r in app.routes if hasattr(r, "methods")}
        for caminho in ("/api/estado", "/api/campanha", "/api/decisao", "/api/velas", "/api/rastreio"):
            assert (caminho, ("GET",)) in rotas
        assert not any("POST" in m or "DELETE" in m for _, m in rotas)
