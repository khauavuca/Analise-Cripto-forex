"""Leituras de grafico: causais, relativas a direcao, e com a confluencia contando os outros."""
from __future__ import annotations

import numpy as np
import pandas as pd

from nucleo.aprendizado import leituras as lt
from nucleo.dados.provedor import reamostrar
from nucleo.estrategias.base import COMPRA, VENDA, quadro_sinais


def velas(fechamento: np.ndarray, freq: str = "1h", semente: int = 1) -> pd.DataFrame:
    gerador = np.random.default_rng(semente)
    anterior = np.r_[fechamento[0], fechamento[:-1]]
    folga = np.abs(gerador.normal(0.3, 0.1, len(fechamento)))
    return pd.DataFrame(
        {
            "abertura": anterior,
            "maxima": np.maximum(fechamento, anterior) + folga,
            "minima": np.minimum(fechamento, anterior) - folga,
            "fechamento": fechamento,
            "volume": np.abs(gerador.normal(100, 25, len(fechamento))) + 1,
        },
        index=pd.date_range("2025-01-01", periods=len(fechamento), freq=freq, tz="UTC"),
    )


def mercado_aleatorio(n: int = 900, semente: int = 11) -> pd.DataFrame:
    gerador = np.random.default_rng(semente)
    return velas(100 + gerador.normal(0, 1, n).cumsum(), semente=semente)


class TestCatalogo:
    def test_toda_leitura_do_catalogo_vira_coluna(self):
        x = lt.calcular(mercado_aleatorio(400), "1h")
        faltando = {l.nome for l in lt.CATALOGO} - set(x.columns)
        assert not faltando, faltando
        assert len(x) == 400

    def test_direcionais_e_monotonas_batem_com_o_catalogo(self):
        assert "estrutura" in lt.DIRECIONAIS and "adx14" not in lt.DIRECIONAIS
        assert "tendencia_maior_a_favor" in lt.MONOTONAS
        assert "momento_20_a_favor" not in lt.MONOTONAS, "momento curto fica livre: reversao entra contra"
        assert "confluencia_a_favor" in lt.MONOTONAS


class TestCausalidade:
    def test_anexar_futuro_nao_muda_o_passado(self):
        quadro = mercado_aleatorio(900)
        cheio = lt.calcular(quadro, "1h")
        parcial = lt.calcular(quadro.iloc[:600], "1h")
        pd.testing.assert_frame_equal(cheio.iloc[:600], parcial, check_dtype=False, rtol=1e-9)

    def test_tendencia_maior_so_usa_vela_maior_ja_fechada(self):
        quadro = mercado_aleatorio(800)
        x = lt.calcular(quadro, "1h")
        maior = reamostrar(quadro, "1h", "4h")
        f = maior["fechamento"]
        e20 = f.ewm(span=20, adjust=False).mean()
        e50 = f.ewm(span=50, adjust=False).mean()
        esperado_por_abertura = pd.Series(
            np.select([(f > e20) & (f > e50), (f < e20) & (f < e50)], [1.0, -1.0], 0.0), index=maior.index
        )
        for i in range(600, 640):
            t = quadro.index[i]
            # A vela de 4h que contem t fecha em (inicio + 4h). Ela so pode ser
            # usada se fechou ate o fechamento da vela de 1h (t + 1h).
            inicio_balde = t.floor("4h")
            fecha_em = inicio_balde + pd.Timedelta("4h")
            usavel = inicio_balde if fecha_em <= t + pd.Timedelta("1h") else inicio_balde - pd.Timedelta("4h")
            assert x["tendencia_maior"].iloc[i] == esperado_por_abertura.loc[usavel], t


class TestEstrutura:
    def test_zigzag_subindo_da_estrutura_de_alta_e_caindo_de_baixa(self):
        i = np.arange(600)
        subindo = velas(100 + 0.05 * i + 2.0 * np.sin(i / 3))
        caindo = velas(200 - 0.05 * i + 2.0 * np.sin(i / 3))
        alta = lt.calcular(subindo, "1h")["estrutura"].dropna().iloc[-200:]
        baixa = lt.calcular(caindo, "1h")["estrutura"].dropna().iloc[-200:]
        assert (alta == 1).mean() > 0.9
        assert (baixa == -1).mean() > 0.9


class TestDirecao:
    def test_no_instante_vira_a_favor_e_desconta_o_proprio_setup(self):
        leituras = pd.DataFrame(
            {"estrutura": [1.0], "adx14": [30.0], "setups_comprando": [3.0], "setups_vendendo": [1.0]}
        )
        compra = lt.no_instante(leituras, 0, COMPRA)
        venda = lt.no_instante(leituras, 0, VENDA)

        assert compra["estrutura_a_favor"] == 1 and venda["estrutura_a_favor"] == -1
        assert compra["adx14"] == 30 and "estrutura" not in compra
        # 3 comprando inclui o proprio setup: 2 outros a favor, 1 contra.
        assert compra["confluencia_a_favor"] == 1 and compra["setups_contra"] == 1
        # 1 vendendo e o proprio: 0 outros a favor, 3 contra.
        assert venda["confluencia_a_favor"] == -3 and venda["setups_contra"] == 3

    def test_confluencia_conta_setups_nao_sinais(self):
        indice = pd.date_range("2025-01-01", periods=10, freq="1h", tz="UTC")
        a = quadro_sinais(indice)
        a.loc[indice[2], "direcao"] = np.int8(COMPRA)
        a.loc[indice[3], "direcao"] = np.int8(COMPRA)
        b = quadro_sinais(indice)
        b.loc[indice[4], "direcao"] = np.int8(VENDA)
        c = lt.confluencia({"a": a, "b": b}, janela=6)

        assert c.loc[indice[4], "setups_comprando"] == 1, "dois sinais do mesmo setup valem um"
        assert c.loc[indice[4], "setups_vendendo"] == 1
        assert c.loc[indice[9], "setups_comprando"] == 0, "sinal de 6 barras atras saiu da janela"
        assert c.loc[indice[9], "setups_vendendo"] == 1

    def test_descrever_em_portugues(self):
        x = {"estrutura_a_favor": 1.0, "tendencia_maior_a_favor": -1.0, "confluencia_a_favor": 0.0, "adx14": 22.0}
        assert lt.descrever(x) == ["estrutura a favor", "grafico maior contra", "confluencia neutra"]
