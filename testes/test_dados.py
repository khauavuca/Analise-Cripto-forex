"""Camada de dados: normalizacao, agregacao e o contrato que falha alto."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from nucleo.dados.armazenamento import subtrair_intervalos, unir_intervalos
from nucleo.dados.provedor import (
    ContratoQuebrado,
    TimeframeInvalido,
    descartar_vela_aberta,
    normalizar,
    reamostrar,
    validar_velas,
)


def velas(quantidade: int, inicio: str = "2025-01-01", freq: str = "1h") -> pd.DataFrame:
    indice = pd.date_range(inicio, periods=quantidade, freq=freq, tz="UTC", name="data_hora")
    passo = np.arange(quantidade, dtype=float)
    return pd.DataFrame(
        {
            "abertura": 100 + passo,
            "maxima": 101 + passo,
            "minima": 99 + passo,
            "fechamento": 100.5 + passo,
            "volume": np.ones(quantidade),
        },
        index=indice,
    )


class TestValidarVelas:
    def test_aceita_quadro_bom(self):
        validar_velas(velas(10))

    def test_pega_o_bug_que_derrubou_o_projeto_antigo(self):
        """DataFrame com o numero certo de linhas e tudo NaN.

        Era o que o conector antigo produzia ao passar `columns=` com nomes que
        nao existiam no payload. As checagens de tamanho passavam, os
        indicadores viravam NaN e o sistema nunca emitia sinal, sem erro nenhum.
        """
        registros = [{"openPrice": 1, "closePrice": 2} for _ in range(60)]
        quadro = pd.DataFrame(
            registros, columns=["abertura", "maxima", "minima", "fechamento", "volume"]
        )
        quadro.index = pd.date_range("2025-01-01", periods=60, freq="1h", tz="UTC")

        assert len(quadro) == 60, "o numero de linhas engana quem so checa tamanho"
        with pytest.raises(ContratoQuebrado, match="nulos"):
            validar_velas(quadro)

    def test_recusa_indice_sem_fuso(self):
        quadro = velas(5)
        quadro.index = quadro.index.tz_localize(None)
        with pytest.raises(ContratoQuebrado, match="UTC"):
            validar_velas(quadro)

    def test_recusa_maxima_abaixo_do_corpo(self):
        quadro = velas(5)
        quadro.loc[quadro.index[2], "maxima"] = 0.0
        with pytest.raises(ContratoQuebrado, match="maxima"):
            validar_velas(quadro)

    def test_recusa_timestamp_repetido(self):
        quadro = pd.concat([velas(3), velas(3)]).sort_index()
        with pytest.raises(ContratoQuebrado, match="repetidos"):
            validar_velas(quadro)


class TestNormalizar:
    def test_ordena_e_remove_duplicatas(self):
        quadro = pd.concat([velas(3), velas(3)])
        assert len(normalizar(quadro.iloc[::-1])) == 3

    def test_quadro_vazio_mantem_formato(self):
        resultado = normalizar(pd.DataFrame())
        assert list(resultado.columns) == [
            "abertura", "maxima", "minima", "fechamento", "volume",
        ]
        assert resultado.index.tz is not None


class TestDescartarVelaAberta:
    def test_corta_a_vela_em_formacao(self):
        quadro = velas(5)
        # "Agora" no meio da ultima vela: ela ainda nao fechou.
        agora = int(quadro.index[-1].timestamp() * 1000) + 30 * 60 * 1000
        resultado = descartar_vela_aberta(quadro, "1h", referencia_ms=agora)
        assert len(resultado) == 4
        assert resultado.index[-1] == quadro.index[-2]

    def test_mantem_vela_ja_fechada(self):
        quadro = velas(5)
        agora = int(quadro.index[-1].timestamp() * 1000) + 60 * 60 * 1000
        assert len(descartar_vela_aberta(quadro, "1h", referencia_ms=agora)) == 5


class TestReamostrar:
    def test_bordas_de_4h_sao_fixas_no_relogio(self):
        """A grade tem que ser 00/04/08/12/16/20 UTC sempre.

        Sem `origin="epoch"` o pandas ancora a grade na primeira vela da fatia
        pedida - e o mesmo backtest passa a dar numeros diferentes so por causa
        da data de inicio que se pediu. Bug de reprodutibilidade silencioso.
        """
        for comeco in ("2025-01-01 00:00", "2025-01-01 03:00", "2025-01-01 07:00"):
            agregado = reamostrar(velas(200, inicio=comeco), "1h", "4h")
            assert set(agregado.index.hour) <= {0, 4, 8, 12, 16, 20}

    def test_ohlc_agrega_certo(self):
        quadro = velas(8, inicio="2025-01-01 00:00")
        agregado = reamostrar(quadro, "1h", "4h")
        primeiro = agregado.iloc[0]
        assert primeiro.abertura == quadro.abertura.iloc[0]
        assert primeiro.fechamento == quadro.fechamento.iloc[3]
        assert primeiro.maxima == quadro.maxima.iloc[:4].max()
        assert primeiro.minima == quadro.minima.iloc[:4].min()
        assert primeiro.volume == quadro.volume.iloc[:4].sum()

    def test_descarta_balde_incompleto_no_fim(self):
        # 6 velas de 1h a partir de 00:00 = um balde cheio + um pela metade.
        agregado = reamostrar(velas(6, inicio="2025-01-01 00:00"), "1h", "4h")
        assert len(agregado) == 1

    def test_descarta_balde_com_buraco_no_meio(self):
        """Contagem pega o que dropna() nao pega.

        Uma vela de 4h montada com 3 velas de 1h nao tem NaN nenhum - ela so
        esta errada. So contar quantas velas entraram no balde revela isso.
        """
        quadro = velas(8, inicio="2025-01-01 00:00")
        com_buraco = quadro.drop(quadro.index[1])
        agregado = reamostrar(com_buraco, "1h", "4h")
        assert len(agregado) == 1
        assert agregado.index[0].hour == 4

    def test_recusa_timeframe_nao_multiplo(self):
        # 6h nao e multiplo inteiro de 4h: a agregacao sairia desalinhada.
        with pytest.raises(TimeframeInvalido, match="multiplo"):
            reamostrar(velas(50), "4h", "6h")

    def test_recusa_destino_menor_que_origem(self):
        with pytest.raises(TimeframeInvalido, match="maior ou igual"):
            reamostrar(velas(50), "4h", "1h")


class TestAritmeticaDeIntervalos:
    def test_unir_junta_sobrepostos_e_encostados(self):
        assert unir_intervalos([(0, 10), (5, 20), (30, 40)]) == [(0, 20), (30, 40)]

    def test_subtrair_devolve_os_buracos(self):
        assert subtrair_intervalos((0, 100), [(20, 40), (60, 80)]) == [
            (0, 20), (40, 60), (80, 100),
        ]

    def test_subtrair_sem_cobertura_devolve_tudo(self):
        assert subtrair_intervalos((0, 100), []) == [(0, 100)]

    def test_subtrair_totalmente_coberto_devolve_vazio(self):
        assert subtrair_intervalos((10, 50), [(0, 100)]) == []
