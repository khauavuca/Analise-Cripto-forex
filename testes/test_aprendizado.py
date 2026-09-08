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


def _conjunto_com_leituras(n: int = 1200, semente: int = 9) -> cj.Conjunto:
    """Dois setups, uma leitura monotona que ajuda de verdade e uma que atrapalha.

    `confluencia_a_favor` tem efeito positivo real. `armadilha_a_favor` e
    construida com efeito NEGATIVO, mas esta na lista das monotonas: o modelo
    e obrigado a nao usa-la contra a logica do grafico.
    """
    gerador = np.random.default_rng(semente)
    confluencia = gerador.integers(-3, 4, n).astype(float)
    tendencia = gerador.integers(-1, 2, n).astype(float)
    ruido = gerador.normal(size=n)
    setup = np.where(gerador.random(n) < 0.5, "a", "b")
    # O setup "b" e melhor que o "a"; a confluencia ajuda os dois; a tendencia
    # maior "a favor" atrapalha DE PROPOSITO neste mundo sintetico.
    logit = 0.6 * confluencia - 0.8 * tendencia + np.where(setup == "b", 0.8, -0.4) + 0.2 * ruido
    p = 1 / (1 + np.exp(-logit))
    venceu = gerador.random(n) < p
    r = np.where(venceu, gerador.uniform(0.5, 2.5, n), -1.0)
    entradas = pd.date_range("2023-01-01", periods=n, freq="14h", tz="UTC")
    x = pd.DataFrame(
        {
            "confluencia_a_favor": confluencia,
            "tendencia_maior_a_favor": tendencia,
            "ruido": ruido,
            "hora": entradas.hour,
            "dia_semana": entradas.dayofweek,
            "setup": setup,
        }
    )
    meta = pd.DataFrame(
        {
            "entrada": entradas, "saida": entradas + pd.Timedelta(hours=20),
            "par": "X", "timeframe": "4h", "estrategia": setup, "motivo_saida": "ALVO",
        }
    )
    rot = pd.DataFrame({"venceu": venceu, "multiplo_r": r, "retorno_liquido_pct": r * 0.01})
    return cj.Conjunto(x, rot, meta)


class TestModeloUnico:
    def test_setup_vira_categoria_e_hora_fica_de_fora(self):
        conjunto = _conjunto_com_leituras()
        filtro = FiltroML().treinar(conjunto)
        assert "setup" in filtro.colunas and filtro.categorias == {"a": 0, "b": 1}
        assert "hora" not in filtro.colunas and "dia_semana" not in filtro.colunas
        # O setup melhor recebe probabilidade maior, tudo o mais igual.
        base = conjunto.entradas.head(1).copy()
        base[["confluencia_a_favor", "tendencia_maior_a_favor", "ruido"]] = [0.0, 0.0, 0.0]
        a, b = base.copy(), base.copy()
        a["setup"], b["setup"] = "a", "b"
        assert filtro.probabilidade(b)[0] > filtro.probabilidade(a)[0]
        # Setup desconhecido nao quebra: vira categoria ausente.
        desconhecido = base.copy()
        desconhecido["setup"] = "zzz"
        assert 0 <= filtro.probabilidade(desconhecido)[0] <= 1

    def test_monotonia_nao_deixa_inverter_a_logica_do_grafico(self):
        conjunto = _conjunto_com_leituras()
        filtro = FiltroML(ConfigFiltro(monotonico=True)).treinar(conjunto)
        base = conjunto.entradas.head(1).copy()
        base[["confluencia_a_favor", "ruido"]] = [0.0, 0.0]
        base["setup"] = "a"
        grade = pd.concat([base.assign(tendencia_maior_a_favor=v) for v in (-1.0, 0.0, 1.0)], ignore_index=True)
        prob = filtro.probabilidade(grade)
        # Nos dados a tendencia maior "a favor" atrapalha; com a restricao o
        # modelo pode no maximo ignora-la, nunca usa-la ao contrario.
        assert prob[0] <= prob[1] + 1e-9 <= prob[2] + 2e-9
        # A confluencia, que ajuda de verdade, continua sendo usada.
        grade = pd.concat([base.assign(confluencia_a_favor=v) for v in (-3.0, 0.0, 3.0)], ignore_index=True)
        prob = filtro.probabilidade(grade)
        assert prob[2] > prob[0] + 0.1

    def test_sem_monotonia_o_modelo_aprende_a_armadilha(self):
        conjunto = _conjunto_com_leituras()
        filtro = FiltroML(ConfigFiltro(monotonico=False)).treinar(conjunto)
        base = conjunto.entradas.head(1).copy()
        base[["confluencia_a_favor", "ruido"]] = [0.0, 0.0]
        base["setup"] = "a"
        grade = pd.concat([base.assign(tendencia_maior_a_favor=v) for v in (-1.0, 1.0)], ignore_index=True)
        prob = filtro.probabilidade(grade)
        assert prob[0] > prob[1], "sem a restricao ele segue o dado, mesmo contra a logica"


class TestRelatorios:
    def test_calibracao_e_peneira(self):
        from nucleo.aprendizado.filtro import calibracao, peneira

        conjunto = _conjunto_com_leituras()
        relatorio = avaliar_walkforward(conjunto, ConfigFiltro(), meses_teste=4, minimo_treino=150)
        cal = relatorio.calibracao()
        assert list(cal.columns) == ["faixa", "n", "prob_media", "acerto_real"]
        assert cal["n"].sum() == sum(j.n_teste for j in relatorio.janelas)
        # Calibrado: na faixa mais alta ganha-se mais do que na mais baixa.
        assert cal["acerto_real"].iloc[-1] > cal["acerto_real"].iloc[0]
        assert "melhorou o total" in relatorio.veredito()

        pen = peneira(conjunto, a_partir_de=relatorio.janelas[0].corte)
        assert {"leitura", "comparacao", "delta_r"} <= set(pen.columns)
        assert "hora" not in pen["leitura"].tolist()
        por_leitura = pen.set_index("leitura")
        assert por_leitura.loc["confluencia_a_favor", "delta_r"] > 0
        assert por_leitura.loc["tendencia_maior_a_favor", "delta_r"] < 0
        assert por_leitura.loc["confluencia_a_favor", "comparacao"] == "contra -> a favor"

        vazio = calibracao(np.array([]), np.array([]))
        assert vazio.empty
