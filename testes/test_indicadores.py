"""Indicadores: causalidade, suavizacao de Wilder e deslocamento do canal."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from nucleo.indicadores import basicos as ind

SEMENTE = 42


@pytest.fixture
def serie() -> pd.Series:
    gerador = np.random.default_rng(SEMENTE)
    passos = gerador.normal(0, 1, 800).cumsum()
    return pd.Series(
        100 + passos,
        index=pd.date_range("2025-01-01", periods=800, freq="1h", tz="UTC"),
    )


@pytest.fixture
def ohlc(serie: pd.Series) -> pd.DataFrame:
    gerador = np.random.default_rng(SEMENTE + 1)
    amplitude = np.abs(gerador.normal(0, 0.5, len(serie)))
    return pd.DataFrame(
        {
            "abertura": serie.shift(1).fillna(serie.iloc[0]),
            "maxima": serie + amplitude,
            "minima": serie - amplitude,
            "fechamento": serie,
        }
    )


class TestCausalidade:
    """O valor da posicao i nao pode mudar quando chegam dados depois dela.

    Se mudar, ha vazamento de futuro - janela centrada, bfill, max de serie
    inteira, z-score sobre a amostra toda. E o teste que vale por dez, porque
    o backtest continua rodando e so devolve um numero bonito e falso.
    """

    def calcular(self, quadro: pd.DataFrame) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "rsi": ind.indice_forca_relativa(quadro.fechamento, 14),
                "atr": ind.faixa_verdadeira_media(
                    quadro.maxima, quadro.minima, quadro.fechamento, 14
                ),
                "adx": ind.indice_direcional_medio(
                    quadro.maxima, quadro.minima, quadro.fechamento, 14
                )["adx"],
                "macd": ind.macd(quadro.fechamento)["histograma"],
                "sma": ind.media_movel_simples(quadro.fechamento, 50),
                "bollinger": ind.bandas_bollinger(quadro.fechamento, 20)["superior"],
                "donchian": ind.canal_donchian(quadro.maxima, quadro.minima, 50)[
                    "resistencia"
                ],
            }
        )

    @pytest.mark.parametrize("corte", [200, 400, 650])
    def test_indicadores_nao_olham_o_futuro(self, ohlc: pd.DataFrame, corte: int):
        completo = self.calcular(ohlc)
        parcial = self.calcular(ohlc.iloc[:corte])
        pd.testing.assert_frame_equal(
            parcial.tail(50), completo.iloc[:corte].tail(50), check_freq=False
        )


class TestForcaRelativa:
    def test_serie_so_de_alta_satura_em_100(self):
        subindo = pd.Series(np.arange(100, 200, dtype=float))
        assert ind.indice_forca_relativa(subindo, 14).iloc[-1] == pytest.approx(100.0)

    def test_serie_so_de_baixa_satura_perto_de_zero(self):
        caindo = pd.Series(np.arange(200, 100, -1, dtype=float))
        assert ind.indice_forca_relativa(caindo, 14).iloc[-1] == pytest.approx(0.0)

    def test_serie_parada_fica_em_50(self):
        parada = pd.Series(np.full(60, 100.0))
        assert ind.indice_forca_relativa(parada, 14).iloc[-1] == pytest.approx(50.0)

    def test_aquecimento_fica_vazio(self, serie: pd.Series):
        """Sem min_periods o pandas emitiria um valor ja na barra 1.

        Seria um "RSI" semeado por uma unica observacao, e o backtest operaria
        aquilo como se fosse indicador pronto.
        """
        resultado = ind.indice_forca_relativa(serie, 14)
        assert resultado.iloc[:13].isna().all()
        assert resultado.iloc[14:].notna().all()

    def test_difere_da_media_simples(self, serie: pd.Series):
        """Wilder e Cutler nao sao a mesma coisa - era o bug do codigo antigo."""
        variacao = serie.diff()
        ganho = variacao.clip(lower=0).rolling(14).mean()
        perda = (-variacao.clip(upper=0)).rolling(14).mean()
        cutler = 100 - 100 / (1 + ganho / perda)
        wilder = ind.indice_forca_relativa(serie, 14)

        comuns = wilder.notna() & cutler.notna()
        assert (wilder[comuns] - cutler[comuns]).abs().mean() > 1.0


class TestFaixaVerdadeira:
    def test_primeira_barra_usa_a_amplitude(self, ohlc: pd.DataFrame):
        faixa = ind.faixa_verdadeira(ohlc.maxima, ohlc.minima, ohlc.fechamento)
        esperado = ohlc.maxima.iloc[0] - ohlc.minima.iloc[0]
        assert faixa.iloc[0] == pytest.approx(esperado)

    def test_captura_gap_contra_fechamento_anterior(self):
        quadro = pd.DataFrame(
            {
                "maxima": [10.0, 20.0],
                "minima": [9.0, 19.0],
                "fechamento": [9.5, 19.5],
            }
        )
        faixa = ind.faixa_verdadeira(quadro.maxima, quadro.minima, quadro.fechamento)
        # A amplitude da barra 2 e 1, mas o gap desde 9,5 ate 20 e 10,5.
        assert faixa.iloc[1] == pytest.approx(10.5)


class TestCanalDonchian:
    def test_deslocado_ignora_a_barra_corrente(self):
        """O codigo antigo fazia tail(50).max() incluindo a propria vela.

        Usar a maxima da barra que se esta analisando como alvo dela e olhar o
        resultado antes de decidir entrar.
        """
        maxima = pd.Series([1.0, 2.0, 3.0, 99.0, 4.0])
        minima = pd.Series([1.0, 2.0, 3.0, 0.5, 4.0])

        deslocado = ind.canal_donchian(maxima, minima, periodo=3)
        assert deslocado["resistencia"].iloc[3] == 3.0  # nao enxerga o 99

        cru = ind.canal_donchian(maxima, minima, periodo=3, deslocar=False)
        assert cru["resistencia"].iloc[3] == 99.0


@pytest.fixture
def ohlcv(ohlc: pd.DataFrame) -> pd.DataFrame:
    gerador = np.random.default_rng(SEMENTE + 2)
    quadro = ohlc.copy()
    quadro["volume"] = np.abs(gerador.normal(100, 30, len(quadro))) + 1
    return quadro


class TestPivos:
    """Onde o look-ahead entra na analise de estrutura."""

    def test_topo_so_aparece_depois_da_confirmacao(self):
        # O topo esta na posicao 3. Com direita=2, ele so pode ser conhecido
        # na posicao 5.
        maxima = pd.Series([1.0, 2.0, 3.0, 9.0, 4.0, 3.0, 2.0, 1.0, 0.5])
        minima = maxima - 1

        resultado = ind.pivos(maxima, minima, esquerda=2, direita=2)

        assert resultado["topo"].iloc[4] != 9.0, "na barra 4 o topo ainda nao existe"
        assert resultado["topo"].iloc[5] == 9.0, "so na barra 5 ele passa a ser visivel"
        assert bool(resultado["topo_novo"].iloc[5]) is True

    def test_topo_e_carregado_para_frente(self):
        maxima = pd.Series([1.0, 2.0, 9.0, 4.0, 3.0, 2.0, 1.0, 0.5, 0.4, 0.3])
        minima = maxima - 1
        resultado = ind.pivos(maxima, minima, esquerda=2, direita=2)
        conhecidos = resultado["topo"].dropna()
        assert (conhecidos == 9.0).any()

    @pytest.mark.parametrize("corte", [200, 400, 650])
    def test_pivos_sao_causais(self, ohlc: pd.DataFrame, corte: int):
        """Cortar a serie nao pode mudar nenhum pivo ja conhecido.

        A janela usada e centrada - olha para frente de proposito - e so o
        `shift` de confirmacao a torna causal. Se esse shift sumir, este teste
        e o unico que percebe.
        """
        completo = ind.pivos(ohlc.maxima, ohlc.minima, 3, 3)
        parcial = ind.pivos(ohlc.maxima.iloc[:corte], ohlc.minima.iloc[:corte], 3, 3)
        pd.testing.assert_frame_equal(parcial.tail(40), completo.iloc[:corte].tail(40))


class TestVwap:
    def test_reinicia_a_cada_ancora(self, ohlcv: pd.DataFrame):
        referencia = ind.vwap_sessao(ohlcv, ancora="D")
        primeiro_dia = ohlcv.index.floor("D")[0]
        primeira_barra = ohlcv[ohlcv.index.floor("D") == primeiro_dia].iloc[0]
        tipico = (primeira_barra.maxima + primeira_barra.minima + primeira_barra.fechamento) / 3
        # Na primeira barra do periodo o VWAP e o proprio preco tipico dela.
        assert referencia["vwap"].iloc[0] == pytest.approx(tipico)
        assert referencia["desvio"].iloc[0] == pytest.approx(0.0, abs=1e-6)

    @pytest.mark.parametrize("corte", [200, 500])
    def test_vwap_e_causal(self, ohlcv: pd.DataFrame, corte: int):
        completo = ind.vwap_sessao(ohlcv, ancora="D")
        parcial = ind.vwap_sessao(ohlcv.iloc[:corte], ancora="D")
        pd.testing.assert_series_equal(
            parcial["vwap"].tail(30), completo["vwap"].iloc[:corte].tail(30)
        )

    def test_ancora_semanal_agrupa_mais_barras(self, ohlcv: pd.DataFrame):
        diario = ind.rotulo_ancora(ohlcv.index, "D")
        semanal = ind.rotulo_ancora(ohlcv.index, "W")
        assert len(set(semanal)) < len(set(diario))


class TestKeltner:
    def test_bandas_cercam_a_media(self, ohlc: pd.DataFrame):
        canal = ind.canal_keltner(ohlc.maxima, ohlc.minima, ohlc.fechamento)
        pronto = canal.dropna()
        assert (pronto["inferior"] < pronto["meio"]).all()
        assert (pronto["superior"] > pronto["meio"]).all()

    def test_largura_acompanha_o_atr(self, ohlc: pd.DataFrame):
        estreito = ind.canal_keltner(ohlc.maxima, ohlc.minima, ohlc.fechamento, multiplo=1.0)
        largo = ind.canal_keltner(ohlc.maxima, ohlc.minima, ohlc.fechamento, multiplo=3.0)
        vale = estreito["superior"].notna()
        assert (largo["superior"][vale] > estreito["superior"][vale]).all()


class TestMacd:
    def test_histograma_e_a_diferenca(self, serie: pd.Series):
        linhas = ind.macd(serie)
        pd.testing.assert_series_equal(
            linhas["histograma"],
            (linhas["macd"] - linhas["sinal"]).rename("histograma"),
        )
