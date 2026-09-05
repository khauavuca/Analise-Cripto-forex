"""Campanha: so conta o que nasceu dentro dela, e cada trader tem a propria banca."""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from nucleo import campanha
from nucleo.backtest.motor import ModeloCustos
from nucleo.estrategias.base import COMPRA, Estrategia, quadro_sinais
from nucleo.risco.carteira import RegrasCarteira


class SempreCompra(Estrategia):
    """Compra a cada N barras, com stop e alvo fixos em percentual."""

    def __init__(self, nome: str, a_cada: int, stop: float, alvo: float):
        self.nome = nome
        self.a_cada = a_cada
        self.stop_pct = stop
        self.alvo_pct = alvo

    def barras_de_aquecimento(self) -> int:
        return 2

    def gerar_sinais(self, quadro: pd.DataFrame) -> pd.DataFrame:
        sinais = quadro_sinais(quadro.index)
        alvo = np.arange(len(quadro)) % self.a_cada == 0
        fechamento = quadro["fechamento"]
        sinais.loc[alvo, "direcao"] = np.int8(COMPRA)
        sinais.loc[alvo, "forca"] = 0.8
        sinais.loc[alvo, "stop"] = (fechamento * (1 - self.stop_pct))[alvo]
        sinais.loc[alvo, "alvo"] = (fechamento * (1 + self.alvo_pct))[alvo]
        return sinais


def mercado(subindo: bool, barras: int = 200) -> pd.DataFrame:
    passo = 0.4 if subindo else -0.4
    fechamento = 100 + passo * np.arange(barras)
    anterior = np.r_[fechamento[0], fechamento[:-1]]
    return pd.DataFrame(
        {
            "abertura": anterior,
            "maxima": np.maximum(fechamento, anterior) + 0.2,
            "minima": np.minimum(fechamento, anterior) - 0.2,
            "fechamento": fechamento,
            "volume": np.ones(barras),
        },
        index=pd.date_range("2026-09-01", periods=barras, freq="1h", tz="UTC"),
    )


def mercado_oscilando(barras: int = 200, periodo: int = 40) -> pd.DataFrame:
    """Sobe metade do periodo, cai a outra metade: da ganhos E perdas."""
    fase = (np.arange(barras) % periodo) < periodo // 2
    passos = np.where(fase, 0.4, -0.4)
    fechamento = 100 + np.cumsum(passos)
    anterior = np.r_[fechamento[0], fechamento[:-1]]
    return pd.DataFrame(
        {
            "abertura": anterior,
            "maxima": np.maximum(fechamento, anterior) + 0.2,
            "minima": np.minimum(fechamento, anterior) - 0.2,
            "fechamento": fechamento,
            "volume": np.ones(barras),
        },
        index=pd.date_range("2026-09-01", periods=barras, freq="1h", tz="UTC"),
    )


def config(inicio="2026-09-04", fim="2026-09-08", banca=500.0) -> campanha.ConfigCampanha:
    return campanha.ConfigCampanha(
        inicio=datetime.fromisoformat(inicio).replace(tzinfo=timezone.utc),
        fim=datetime.fromisoformat(fim).replace(tzinfo=timezone.utc),
        banca=banca,
        moeda="BRL",
        pares=("X/USDT",),
        timeframes=("1h",),
        regras=RegrasCarteira(max_posicoes=9, max_por_par=9, exposicao_maxima=9,
                              perda_diaria_maxima=0, perdas_seguidas_para_pausa=99,
                              valor_minimo_ordem=1.0),
        custos=ModeloCustos(0.0, 0.0),
    )


class TestRecorte:
    def test_ignora_operacoes_anteriores_ao_inicio(self):
        quadros = {("X/USDT", "1h"): mercado(subindo=True)}
        trader = SempreCompra("t", a_cada=10, stop=0.05, alvo=0.02)
        resultado = campanha.avaliar(quadros, [trader], config())

        t = resultado.traders[0]
        assert not t.fechadas.empty
        assert (t.fechadas["entrada"] >= pd.Timestamp("2026-09-04", tz="UTC")).all()

    def test_ignora_velas_depois_do_fim(self):
        quadros = {("X/USDT", "1h"): mercado(subindo=True)}
        trader = SempreCompra("t", a_cada=10, stop=0.05, alvo=0.02)
        resultado = campanha.avaliar(quadros, [trader], config(fim="2026-09-05"))
        assert resultado.ultima_vela < pd.Timestamp("2026-09-05", tz="UTC")
        assert (resultado.traders[0].fechadas["saida"] < pd.Timestamp("2026-09-05", tz="UTC")).all()


class TestTraders:
    def test_cada_trader_tem_a_propria_banca(self):
        quadros = {("X/USDT", "1h"): mercado(subindo=True)}
        bom = SempreCompra("bom", a_cada=10, stop=0.05, alvo=0.02)
        # Stop a 0,1% com velas que oscilam 0,2 abaixo da abertura: estopa
        # sempre. (0,5% nao estopava - a vela nao chegava la - e o "ruim"
        # saia por tempo com lucro num mercado subindo.)
        ruim = SempreCompra("ruim", a_cada=10, stop=0.001, alvo=0.50)
        resultado = campanha.avaliar(quadros, [bom, ruim], config())

        por_nome = {t.nome: t for t in resultado.traders}
        assert por_nome["bom"].saldo > 500 > por_nome["ruim"].saldo
        # O bom nao "paga" pelas perdas do ruim: bancas independentes.
        assert por_nome["bom"].perdidas == 0 or por_nome["bom"].ganhas > 0

    def test_ranking_ordena_por_banca(self):
        quadros = {("X/USDT", "1h"): mercado(subindo=True)}
        traders = [
            SempreCompra("ruim", a_cada=10, stop=0.001, alvo=0.50),
            SempreCompra("bom", a_cada=10, stop=0.05, alvo=0.02),
        ]
        ranking = campanha.avaliar(quadros, traders, config()).ranking()
        assert ranking["trader"].tolist() == ["bom", "ruim"]
        assert ranking["banca_inicial"].iloc[0] == 500.0


class TestRelatorio:
    def test_texto_e_para_leigo(self):
        quadros = {("X/USDT", "1h"): mercado(subindo=True)}
        trader = SempreCompra("bom", a_cada=10, stop=0.05, alvo=0.02)
        resultado = campanha.avaliar(quadros, [trader], config())
        texto = campanha.relatorio_simples(resultado)

        assert "R$ 500,00" in texto
        assert "ganhas" in texto and "perdidas" in texto
        assert "Nenhuma ordem real" in texto
        # "payoff" entra no relatorio, mas explicado em portugues claro.
        for jargao in ("AUC", "expectancia", "IC95"):
            assert jargao not in texto
        assert "cada ganho paga" in texto

    def test_mostra_acerto_payoff_e_ponto_de_empate(self):
        quadros = {("X/USDT", "1h"): mercado_oscilando()}
        trader = SempreCompra("misto", a_cada=5, stop=0.03, alvo=0.02)
        resultado = campanha.avaliar(quadros, [trader], config(inicio="2026-09-02"))
        r = resultado.ranking().iloc[0]

        assert r.ganhas > 0 and r.perdidas > 0, "o mercado oscilando tem que dar os dois"
        assert r.operacoes == r.ganhas + r.perdidas
        assert r.acerto == pytest.approx(r.ganhas / r.operacoes)
        assert r.acerto_de <= r.acerto <= r.acerto_ate, "faixa contem o acerto observado"

        fechamentos = resultado.traders[0].carteira.fechamentos["resultado"]
        ganho_medio = fechamentos[fechamentos > 0].mean()
        perda_media = abs(fechamentos[fechamentos <= 0].mean())
        assert r.payoff == pytest.approx(ganho_medio / perda_media)
        # Com payoff p, empata-se acertando 1/(1+p).
        assert r.acerto_para_empatar == pytest.approx(1 / (1 + r.payoff))
        assert r.media_por_operacao == pytest.approx(fechamentos.mean())

        texto = campanha.relatorio_simples(resultado)
        assert "payoff" in texto and "para empatar" in texto and "acerto" in texto

    def test_payoff_degenerado_nao_inventa_ponto_de_empate(self):
        quadros = {("X/USDT", "1h"): mercado(subindo=True)}
        so_ganha = SempreCompra("so_ganha", a_cada=10, stop=0.05, alvo=0.02)
        so_perde = SempreCompra("so_perde", a_cada=10, stop=0.001, alvo=0.50)
        resultado = campanha.avaliar(quadros, [so_ganha, so_perde], config())
        por_nome = {t.nome: t for t in resultado.traders}

        assert por_nome["so_ganha"].payoff == float("inf")
        assert por_nome["so_perde"].payoff == 0.0
        texto = campanha.relatorio_simples(resultado)
        assert "so ganhos ate agora" in texto and "so perdas ate agora" in texto
        assert "para empatar" not in texto.split("Como ler:")[0]

    def test_avisa_quando_e_cedo(self):
        quadros = {("X/USDT", "1h"): mercado(subindo=True)}
        trader = SempreCompra("bom", a_cada=40, stop=0.05, alvo=0.02)  # poucas operacoes
        texto = campanha.relatorio_simples(campanha.avaliar(quadros, [trader], config()))
        assert "AINDA E CEDO" in texto

    def test_json_serializa(self):
        import json

        quadros = {("X/USDT", "1h"): mercado(subindo=True)}
        trader = SempreCompra("bom", a_cada=10, stop=0.05, alvo=0.02)
        dados = campanha.para_json(campanha.avaliar(quadros, [trader], config()))
        texto = json.dumps(dados, default=str)
        assert '"ranking"' in texto and '"traders"' in texto
