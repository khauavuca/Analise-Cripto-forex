"""Motor de backtest - os testes que impedem o resultado de mentir.

Sao os mais importantes do projeto. Um bug aqui nao quebra nada: ele devolve um
numero plausivel, bonito e errado, e a pessoa opera dinheiro em cima dele.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from nucleo.backtest import motor
from nucleo.backtest.motor import ConfigExecucao, ModeloCustos
from nucleo.estrategias.base import COMPRA, NEUTRO, VENDA, quadro_sinais

SEM_CUSTO = ModeloCustos(taxa_por_lado=0.0, slippage_por_lado=0.0)
LONGO_PRAZO = ConfigExecucao(max_barras_no_trade=10_000)


def montar(barras: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    """Cada barra e (abertura, maxima, minima, fechamento)."""
    indice = pd.date_range("2025-01-01", periods=len(barras), freq="1h", tz="UTC")
    return pd.DataFrame(
        {
            "abertura": [b[0] for b in barras],
            "maxima": [b[1] for b in barras],
            "minima": [b[2] for b in barras],
            "fechamento": [b[3] for b in barras],
            "volume": np.ones(len(barras)),
        },
        index=indice,
    )


def sinal_em(quadro, posicao, direcao, stop, alvo):
    sinais = quadro_sinais(quadro.index)
    sinais.iloc[posicao, sinais.columns.get_loc("direcao")] = direcao
    sinais.iloc[posicao, sinais.columns.get_loc("stop")] = stop
    sinais.iloc[posicao, sinais.columns.get_loc("alvo")] = alvo
    sinais.iloc[posicao, sinais.columns.get_loc("forca")] = 1.0
    return sinais


class TestDeslocamentoDeExecucao:
    """A defesa numero um contra look-ahead."""

    def test_entrada_sai_na_abertura_da_barra_seguinte(self):
        quadro = montar([(100, 101, 99, 100)] * 3 + [(200, 260, 199, 250)] * 3)
        sinais = sinal_em(quadro, 2, COMPRA, stop=1.0, alvo=1e9)

        resultado = motor.executar(quadro, sinais, SEM_CUSTO, LONGO_PRAZO)
        trade = resultado.trades.iloc[0]

        assert trade.entrada == quadro.index[3], "sinal na barra 2 executa na barra 3"
        assert trade.preco_entrada == quadro.abertura.iloc[3]
        assert trade.preco_entrada != quadro.fechamento.iloc[2], (
            "executar no fechamento da barra que gerou o sinal e negociar ao "
            "preco que foi entrada da propria decisao"
        )

    def test_atraso_maior_adia_a_entrada(self):
        quadro = montar([(100 + i, 105 + i, 95 + i, 100 + i) for i in range(8)])
        sinais = sinal_em(quadro, 1, COMPRA, stop=1.0, alvo=1e9)

        config = ConfigExecucao(max_barras_no_trade=10_000, atraso_barras=3)
        resultado = motor.executar(quadro, sinais, SEM_CUSTO, config)
        assert resultado.trades.iloc[0].entrada == quadro.index[4]


class TestConsistenciaGlobal:
    def test_posicao_sempre_comprada_equivale_a_comprar_e_segurar(self):
        """Sanidade de ponta a ponta: sem custo, tem que bater exatamente."""
        gerador = np.random.default_rng(7)
        precos = 100 + gerador.normal(0, 1, 60).cumsum()
        quadro = montar([(p, p + 2, p - 2, p) for p in precos])

        sinais = quadro_sinais(quadro.index)
        sinais["direcao"] = np.int8(COMPRA)
        sinais["stop"] = 0.01
        sinais["alvo"] = 1e9
        sinais["forca"] = 1.0

        resultado = motor.executar(quadro, sinais, SEM_CUSTO, LONGO_PRAZO)

        assert len(resultado.trades) == 1
        trade = resultado.trades.iloc[0]
        assert trade.motivo_saida == motor.MOTIVO_FIM
        esperado = quadro.fechamento.iloc[-1] / quadro.abertura.iloc[1] - 1
        assert trade.retorno_liquido_pct == pytest.approx(esperado)

    def test_custos_descontam_exatamente_ida_e_volta(self):
        gerador = np.random.default_rng(7)
        precos = 100 + gerador.normal(0, 1, 60).cumsum()
        quadro = montar([(p, p + 2, p - 2, p) for p in precos])

        sinais = quadro_sinais(quadro.index)
        sinais["direcao"] = np.int8(COMPRA)
        sinais["stop"] = 0.01
        sinais["alvo"] = 1e9

        custos = ModeloCustos(taxa_por_lado=0.001, slippage_por_lado=0.0005)
        resultado = motor.executar(quadro, sinais, custos, LONGO_PRAZO)
        trade = resultado.trades.iloc[0]

        entrada = quadro.abertura.iloc[1] * 1.0005
        saida = quadro.fechamento.iloc[-1] * 0.9995
        assert trade.retorno_liquido_pct == pytest.approx(saida / entrada - 1 - 0.002)
        assert trade.retorno_liquido_pct < trade.retorno_bruto_pct


class TestSaidas:
    def test_stop_tocado_sai_no_preco_do_stop(self):
        quadro = montar(
            [(100, 101, 99, 100), (100, 101, 99, 100), (100, 101, 90, 95)]
        )
        sinais = sinal_em(quadro, 0, COMPRA, stop=95.0, alvo=1e9)
        trade = motor.executar(quadro, sinais, SEM_CUSTO, LONGO_PRAZO).trades.iloc[0]

        assert trade.motivo_saida == motor.MOTIVO_STOP
        assert trade.preco_saida == 95.0

    def test_gap_na_abertura_preenche_na_abertura_nao_no_stop(self):
        """A barra abre abaixo do stop: o preenchimento e pior que o stop.

        Assumir o preco do stop aqui esconde justamente as perdas de cauda -
        as que quebram a conta.
        """
        quadro = montar(
            [(100, 101, 99, 100), (100, 101, 99, 100), (80, 82, 78, 80)]
        )
        sinais = sinal_em(quadro, 0, COMPRA, stop=95.0, alvo=1e9)
        trade = motor.executar(quadro, sinais, SEM_CUSTO, LONGO_PRAZO).trades.iloc[0]

        assert trade.motivo_saida == motor.MOTIVO_STOP
        assert trade.preco_saida == 80.0, "preenche na abertura, nao a 95"

    def test_barra_ambigua_assume_o_stop(self):
        """Toca stop e alvo na mesma barra: o OHLC nao diz qual veio primeiro."""
        quadro = montar(
            [(100, 101, 99, 100), (100, 101, 99, 100), (100, 120, 90, 110)]
        )
        sinais = sinal_em(quadro, 0, COMPRA, stop=95.0, alvo=115.0)

        resultado = motor.executar(quadro, sinais, SEM_CUSTO, LONGO_PRAZO)
        trade = resultado.trades.iloc[0]

        assert trade.motivo_saida == motor.MOTIVO_STOP
        assert bool(trade.ambiguo) is True
        assert resultado.diagnosticos["saidas_ambiguas"] == 1

    def test_convencao_otimista_muda_o_resultado(self):
        quadro = montar(
            [(100, 101, 99, 100), (100, 101, 99, 100), (100, 120, 90, 110)]
        )
        sinais = sinal_em(quadro, 0, COMPRA, stop=95.0, alvo=115.0)
        config = ConfigExecucao(max_barras_no_trade=10_000, ambiguidade="otimista")

        trade = motor.executar(quadro, sinais, SEM_CUSTO, config).trades.iloc[0]
        assert trade.motivo_saida == motor.MOTIVO_ALVO
        assert trade.preco_saida == 115.0

    def test_saida_por_tempo(self):
        quadro = montar([(100, 101, 99, 100)] * 10)
        sinais = sinal_em(quadro, 0, COMPRA, stop=1.0, alvo=1e9)
        config = ConfigExecucao(max_barras_no_trade=3)

        trade = motor.executar(quadro, sinais, SEM_CUSTO, config).trades.iloc[0]
        assert trade.motivo_saida == motor.MOTIVO_TEMPO
        assert trade.barras_no_trade == 3

    def test_venda_espelha_a_compra(self):
        quadro = montar(
            [(100, 101, 99, 100), (100, 101, 99, 100), (100, 101, 80, 85)]
        )
        sinais = sinal_em(quadro, 0, VENDA, stop=110.0, alvo=85.0)
        trade = motor.executar(quadro, sinais, SEM_CUSTO, LONGO_PRAZO).trades.iloc[0]

        assert trade.motivo_saida == motor.MOTIVO_ALVO
        assert trade.preco_saida == 85.0
        assert trade.retorno_bruto_pct > 0, "vendido ganha quando o preco cai"


class TestEstadoDaPosicao:
    def test_nao_abre_posicao_na_barra_em_que_saiu(self):
        """Entrar na abertura de uma barra em que ainda havia posicao seria
        entrar antes da saida que aconteceu no meio dela."""
        quadro = montar([(100, 101, 99, 100)] * 2 + [(100, 101, 90, 95)] * 4)
        sinais = quadro_sinais(quadro.index)
        sinais["direcao"] = np.int8(COMPRA)
        sinais["stop"] = 95.0
        sinais["alvo"] = 1e9

        trades = motor.executar(quadro, sinais, SEM_CUSTO, LONGO_PRAZO).trades
        assert len(trades) >= 2, "o cenario precisa de reentradas para valer"

        # O invariante: cada entrada acontece depois da saida da anterior.
        # (Um trade abrir e fechar na mesma barra e permitido apenas no
        # fechamento forcado do fim dos dados, que nao e reentrada.)
        for anterior, seguinte in zip(trades.itertuples(), trades.iloc[1:].itertuples()):
            assert seguinte.entrada > anterior.saida

    def test_mfe_e_mae_registram_os_extremos(self):
        quadro = montar(
            [(100, 101, 99, 100), (100, 130, 80, 100), (100, 101, 99, 100)]
        )
        sinais = sinal_em(quadro, 0, COMPRA, stop=1.0, alvo=1e9)
        trade = motor.executar(quadro, sinais, SEM_CUSTO, LONGO_PRAZO).trades.iloc[0]

        assert trade.mfe_pct == pytest.approx(0.30)
        assert trade.mae_pct == pytest.approx(-0.20)

    def test_sem_sinal_nao_gera_trade(self):
        quadro = montar([(100, 101, 99, 100)] * 5)
        sinais = quadro_sinais(quadro.index)
        resultado = motor.executar(quadro, sinais, SEM_CUSTO, LONGO_PRAZO)

        assert resultado.trades.empty
        assert (resultado.curva_capital == 1.0).all()

    def test_sinal_sem_protecao_e_ignorado(self):
        quadro = montar([(100, 101, 99, 100)] * 5)
        sinais = quadro_sinais(quadro.index)
        sinais.iloc[1, sinais.columns.get_loc("direcao")] = COMPRA
        # stop e alvo continuam NaN
        assert motor.executar(quadro, sinais, SEM_CUSTO, LONGO_PRAZO).trades.empty


class TestStopNoEmpate:
    """Sobe o stop para a entrada depois de um lucro minimo."""

    def cenario(self):
        # Entrada a 100, stop a 90 (risco de 10%). A barra 2 sobe ate 106
        # (0,6R a favor) e a barra 3 volta para 95.
        return montar(
            [
                (100, 101, 99, 100),
                (100, 101, 99, 100),
                (100, 106, 99, 105),
                (105, 106, 95, 96),
                (96, 97, 95, 96),
            ]
        )

    def test_sem_gatilho_o_trade_sobrevive(self):
        quadro = self.cenario()
        sinais = sinal_em(quadro, 0, COMPRA, stop=90.0, alvo=200.0)
        trades = motor.executar(quadro, sinais, SEM_CUSTO, LONGO_PRAZO).trades
        # 95 nunca toca 90, entao so o fim dos dados fecha a posicao.
        assert trades.iloc[0].motivo_saida == motor.MOTIVO_FIM

    def test_com_gatilho_sai_no_empate(self):
        quadro = self.cenario()
        sinais = sinal_em(quadro, 0, COMPRA, stop=90.0, alvo=200.0)
        config = ConfigExecucao(max_barras_no_trade=10_000, gatilho_empate=0.5)

        trade = motor.executar(quadro, sinais, SEM_CUSTO, config).trades.iloc[0]
        assert trade.motivo_saida == motor.MOTIVO_STOP
        assert trade.preco_saida == pytest.approx(100.0), "sai no preco de entrada"
        assert trade.retorno_bruto_pct == pytest.approx(0.0)

    def test_r_continua_medido_pelo_risco_original(self):
        """Se R usasse o stop movido, a unidade encolheria e inflaria tudo."""
        quadro = self.cenario()
        sinais = sinal_em(quadro, 0, COMPRA, stop=90.0, alvo=200.0)
        config = ConfigExecucao(max_barras_no_trade=10_000, gatilho_empate=0.5)

        trade = motor.executar(quadro, sinais, SEM_CUSTO, config).trades.iloc[0]
        assert trade.multiplo_r == pytest.approx(0.0)
        assert trade.stop == 90.0, "o trade registra o stop com que nasceu"

    def test_gatilho_alto_demais_nao_dispara(self):
        quadro = self.cenario()
        sinais = sinal_em(quadro, 0, COMPRA, stop=90.0, alvo=200.0)
        config = ConfigExecucao(max_barras_no_trade=10_000, gatilho_empate=2.0)
        trades = motor.executar(quadro, sinais, SEM_CUSTO, config).trades
        assert trades.iloc[0].motivo_saida == motor.MOTIVO_FIM


class TestBarraDeEntrada:
    """O stop pode ser atingido na propria barra em que se entrou.

    Entra-se na abertura; o resto da barra acontece depois. Ignorar isso
    garantia que todo trade sobrevivesse a primeira barra - foi como um
    trader de teste com stop de 0,1% ganhou 123% num mercado subindo.
    """

    def test_stop_na_barra_de_entrada(self):
        quadro = montar([(100, 101, 99, 100), (100, 101, 94, 96), (96, 97, 95, 96)])
        sinais = sinal_em(quadro, 0, COMPRA, stop=95.0, alvo=1e9)
        trade = motor.executar(quadro, sinais, SEM_CUSTO, LONGO_PRAZO).trades.iloc[0]

        assert trade.entrada == quadro.index[1]
        assert trade.saida == quadro.index[1], "sai na mesma barra"
        assert trade.motivo_saida == motor.MOTIVO_STOP
        assert trade.preco_saida == 95.0
        assert trade.barras_no_trade == 0

    def test_alvo_na_barra_de_entrada(self):
        quadro = montar([(100, 101, 99, 100), (100, 110, 99, 105), (105, 106, 104, 105)])
        sinais = sinal_em(quadro, 0, COMPRA, stop=90.0, alvo=108.0)
        trade = motor.executar(quadro, sinais, SEM_CUSTO, LONGO_PRAZO).trades.iloc[0]
        assert trade.motivo_saida == motor.MOTIVO_ALVO
        assert trade.preco_saida == 108.0
        assert trade.barras_no_trade == 0

    def test_ambiguidade_na_barra_de_entrada_assume_stop(self):
        quadro = montar([(100, 101, 99, 100), (100, 120, 90, 110), (110, 111, 109, 110)])
        sinais = sinal_em(quadro, 0, COMPRA, stop=95.0, alvo=115.0)
        resultado = motor.executar(quadro, sinais, SEM_CUSTO, LONGO_PRAZO)
        assert resultado.trades.iloc[0].motivo_saida == motor.MOTIVO_STOP
        assert resultado.diagnosticos["saidas_ambiguas"] == 1

    def test_gap_nao_se_aplica_na_entrada(self):
        """A abertura da barra de entrada E o preco de entrada: nao e gap."""
        quadro = montar([(100, 101, 99, 100), (100, 103, 99.5, 102), (102, 103, 101, 102)])
        # Stop acima da abertura de entrada seria "gap" pela regra das barras
        # seguintes; na entrada, a protecao invalida ja foi barrada antes.
        sinais = sinal_em(quadro, 0, COMPRA, stop=99.0, alvo=1e9)
        trades = motor.executar(quadro, sinais, SEM_CUSTO, LONGO_PRAZO).trades
        assert trades.iloc[0].motivo_saida == motor.MOTIVO_FIM, "nao estopa: minima 99,5 > 99"


class TestMultiploR:
    def test_r_bate_com_a_distancia_do_stop(self):
        quadro = montar(
            [(100, 101, 99, 100), (100, 101, 99, 100), (100, 121, 99, 120)]
        )
        # Risco de 10 (100 -> 90); alvo a 120 e um ganho de 20 = 2R.
        sinais = sinal_em(quadro, 0, COMPRA, stop=90.0, alvo=120.0)
        trade = motor.executar(quadro, sinais, SEM_CUSTO, LONGO_PRAZO).trades.iloc[0]

        assert trade.preco_saida == 120.0
        assert trade.multiplo_r == pytest.approx(2.0)
