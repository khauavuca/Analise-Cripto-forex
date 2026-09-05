"""Conjunto de treino e filtro de ML: normalizacao, purga e o controle embaralhado."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from nucleo.aprendizado import conjunto as cj
from nucleo.aprendizado.filtro import ConfigFiltro, FiltroML, avaliar_walkforward
from nucleo.backtest.motor import ModeloCustos, executar
from nucleo.estrategias.cruzamento_ema import EstrategiaCruzamentoEma


@pytest.fixture
def mercado() -> pd.DataFrame:
    gerador = np.random.default_rng(5)
    fechamento = 100 + gerador.normal(0, 1.5, 1200).cumsum()
    amplitude = np.abs(gerador.normal(0, 0.7, 1200)) + 0.05
    anterior = np.r_[fechamento[0], fechamento[:-1]]
    return pd.DataFrame(
        {
            "abertura": anterior,
            "maxima": np.maximum(fechamento, anterior) + amplitude,
            "minima": np.minimum(fechamento, anterior) - amplitude,
            "fechamento": fechamento,
            "volume": np.abs(gerador.normal(100, 20, 1200)) + 1,
        },
        index=pd.date_range("2024-01-01", periods=1200, freq="4h", tz="UTC"),
    )


class TestNormalizacao:
    def test_nivel_de_preco_vira_distancia_relativa(self):
        fechamento = pd.Series([100.0, 200.0])
        painel = pd.DataFrame({"sma_curta": [110.0, 180.0], "rsi": [55.0, 45.0]})
        saida = cj.normalizar_painel(painel, fechamento)
        assert "sma_curta_rel" in saida.columns and "sma_curta" not in saida.columns
        assert saida["sma_curta_rel"].tolist() == pytest.approx([0.10, -0.10])
        assert saida["rsi"].tolist() == [55.0, 45.0], "adimensional fica como esta"

    def test_diferenca_de_preco_e_dividida_pelo_fechamento(self):
        fechamento = pd.Series([100.0, 100.0])
        painel = pd.DataFrame({"atr": [2.0, 4.0], "vwap_desvio": [1.0, 3.0]})
        saida = cj.normalizar_painel(painel, fechamento)
        assert saida["atr_rel"].tolist() == pytest.approx([0.02, 0.04])
        # vwap_desvio comeca com "vwap" mas e diferenca, nao nivel: nao subtrai 1.
        assert saida["vwap_desvio_rel"].tolist() == pytest.approx([0.01, 0.03])

    def test_prefixo_da_composta_e_respeitado(self):
        fechamento = pd.Series([100.0])
        painel = pd.DataFrame({"c1_ema_lenta": [95.0], "c0_rsi": [30.0]})
        saida = cj.normalizar_painel(painel, fechamento)
        assert saida["c1_ema_lenta_rel"].iloc[0] == pytest.approx(-0.05)
        assert saida["c0_rsi"].iloc[0] == 30.0


class TestMontagem:
    def test_usa_a_barra_do_sinal_e_nao_a_da_entrada(self, mercado):
        estrategia = EstrategiaCruzamentoEma()
        trades = executar(mercado, estrategia.gerar_sinais(mercado), ModeloCustos(0, 0)).trades
        conjunto = cj.montar(mercado, estrategia, trades, par="X/USDT", timeframe="4h")
        assert not conjunto.vazio

        fechado = trades[trades.motivo_saida != "FIM_DADOS"].iloc[0]
        pos_entrada = mercado.index.get_loc(fechado.entrada)
        contexto = cj.contexto_de_mercado(mercado)
        # ret_1 na linha do conjunto tem que ser o da barra ANTERIOR a entrada.
        assert conjunto.entradas["ret_1"].iloc[0] == pytest.approx(contexto["ret_1"].iloc[pos_entrada - 1])
        assert conjunto.entradas["ret_1"].iloc[0] != pytest.approx(contexto["ret_1"].iloc[pos_entrada])

    def test_rotulo_e_meta_alinhados(self, mercado):
        estrategia = EstrategiaCruzamentoEma()
        trades = executar(mercado, estrategia.gerar_sinais(mercado), ModeloCustos(0, 0)).trades
        conjunto = cj.montar(mercado, estrategia, trades)
        assert len(conjunto.entradas) == len(conjunto.rotulos) == len(conjunto.meta)
        assert set(cj.COLUNAS_ROTULO) <= set(conjunto.rotulos.columns)


class TestPurga:
    def test_treino_nao_contem_trade_que_fechou_depois_do_corte(self):
        meta = pd.DataFrame(
            {
                "entrada": pd.to_datetime(["2024-01-01", "2024-01-10", "2024-02-01", "2024-03-01"], utc=True),
                "saida": pd.to_datetime(["2024-01-05", "2024-02-05", "2024-02-10", "2024-03-05"], utc=True),
                "par": "X", "timeframe": "4h", "estrategia": "e", "motivo_saida": "STOP",
            }
        )
        conjunto = cj.Conjunto(
            pd.DataFrame({"f": [1.0, 2.0, 3.0, 4.0]}),
            pd.DataFrame({"venceu": [1, 0, 1, 0], "multiplo_r": [1.0, -1, 1, -1], "retorno_liquido_pct": [0.1, -0.1, 0.1, -0.1]}),
            meta,
        )
        treino, teste = cj.dividir_por_tempo(conjunto, pd.Timestamp("2024-02-01", tz="UTC"))
        # O segundo trade entrou em 10/01 mas so fechou em 05/02: rotulo desconhecido no corte.
        assert treino.entradas["f"].tolist() == [1.0]
        assert teste.entradas["f"].tolist() == [3.0, 4.0]


def _conjunto_sintetico(n: int = 900, sinal: bool = True, semente: int = 3) -> cj.Conjunto:
    """Um problema aprendivel (ou nao), com datas espalhadas por dois anos."""
    gerador = np.random.default_rng(semente)
    x = pd.DataFrame(
        {
            "a": gerador.normal(size=n),
            "b": gerador.normal(size=n),
            "ruido": gerador.normal(size=n),
        }
    )
    if sinal:
        p = 1 / (1 + np.exp(-(1.8 * x["a"] - 1.2 * x["b"])))
    else:
        p = np.full(n, 0.45)
    venceu = gerador.random(n) < p
    r = np.where(venceu, gerador.uniform(0.5, 2.5, n), -1.0)
    entradas = pd.date_range("2023-01-01", periods=n, freq="19h", tz="UTC")
    meta = pd.DataFrame(
        {
            "entrada": entradas,
            "saida": entradas + pd.Timedelta(hours=30),
            "par": "X", "timeframe": "4h", "estrategia": "e", "motivo_saida": "ALVO",
        }
    )
    rot = pd.DataFrame({"venceu": venceu, "multiplo_r": r, "retorno_liquido_pct": r * 0.01})
    return cj.Conjunto(x, rot, meta)


class TestFiltro:
    def test_aprende_quando_ha_sinal(self):
        conjunto = _conjunto_sintetico(sinal=True)
        relatorio = avaliar_walkforward(conjunto, ConfigFiltro(), meses_teste=4, minimo_treino=150)
        assert relatorio.janelas, "precisa de janelas para avaliar"
        assert relatorio.real["auc_medio"] > 0.70
        # O controle embaralhado nao pode aprender o que nao existe.
        assert relatorio.embaralhado["auc_medio"] < 0.60
        assert "sinal acima do controle" in relatorio.veredito()

    def test_nao_inventa_sinal_onde_nao_ha(self):
        conjunto = _conjunto_sintetico(sinal=False)
        relatorio = avaliar_walkforward(conjunto, ConfigFiltro(), meses_teste=4, minimo_treino=150)
        assert relatorio.real["auc_medio"] < 0.60
        assert "Nao usar" in relatorio.veredito()

    def test_colunas_ficam_travadas_no_treino(self):
        conjunto = _conjunto_sintetico()
        filtro = FiltroML().treinar(conjunto)
        assert filtro.colunas == ["a", "b", "ruido"]
        # Coluna nova na inferencia e ignorada; coluna faltando vira NaN.
        fora_de_ordem = conjunto.entradas[["ruido", "a"]].copy()
        fora_de_ordem["extra"] = 1.0
        prob = filtro.probabilidade(fora_de_ordem.head(5))
        assert prob.shape == (5,)
        assert np.all((prob >= 0) & (prob <= 1))

    def test_salvar_e_carregar(self, tmp_path):
        conjunto = _conjunto_sintetico()
        filtro = FiltroML().treinar(conjunto)
        caminho = tmp_path / "filtro.pkl"
        filtro.salvar(str(caminho))
        carregado = FiltroML.carregar(str(caminho))
        original = filtro.probabilidade(conjunto.entradas.head(10))
        assert carregado.probabilidade(conjunto.entradas.head(10)) == pytest.approx(original)
