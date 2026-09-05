"""Carteira: as regras que impedem a banca de quebrar, uma por uma."""
from __future__ import annotations

import pandas as pd
import pytest

from nucleo.risco.carteira import Carteira, RegrasCarteira, simular_carteira

T0 = pd.Timestamp("2025-01-01 00:00", tz="UTC")


def em(horas: float) -> pd.Timestamp:
    return T0 + pd.Timedelta(hours=horas)


def regras(**mudancas) -> RegrasCarteira:
    base = dict(saldo_inicial=100.0, risco_por_trade=0.02, max_posicoes=3, max_por_par=1,
                exposicao_maxima=1.0, perda_diaria_maxima=0.06, perdas_seguidas_para_pausa=4,
                sinais_de_pausa=3, valor_minimo_ordem=1.0)
    base.update(mudancas)
    return RegrasCarteira(**base)


class TestDimensionamento:
    def test_risco_fixo_sobre_a_banca_atual(self):
        c = Carteira(regras())
        # Stop a 2%: arriscar 2% da banca (2,00) exige posicao de 100.
        assert c.dimensionar(100.0, 98.0) == pytest.approx(100.0)
        # Stop a 4%: metade da posicao para o mesmo risco.
        assert c.dimensionar(100.0, 96.0) == pytest.approx(50.0)

    def test_stop_apertado_e_limitado_pela_exposicao(self):
        c = Carteira(regras())
        # Stop a 0,5% pediria 400; a exposicao maxima segura em 100.
        assert c.dimensionar(100.0, 99.5) == pytest.approx(100.0)

    def test_posicao_aberta_reduz_o_espaco(self):
        c = Carteira(regras())
        c.abrir("a", "BTC", "e", 1, em(0), 100.0, 96.0)   # usa 50
        assert c.dimensionar(100.0, 96.0) == pytest.approx(50.0)  # sobram 50


class TestLimites:
    def test_teto_de_posicoes(self):
        c = Carteira(regras(max_posicoes=2, max_por_par=5, exposicao_maxima=10))
        assert c.abrir("a", "BTC", "e", 1, em(0), 100, 96) is not None
        assert c.abrir("b", "ETH", "e", 1, em(0), 100, 96) is not None
        assert c.abrir("c", "SOL", "e", 1, em(0), 100, 96) is None
        assert c.recusas[-1].motivo == "teto de posicoes"

    def test_uma_posicao_por_par(self):
        c = Carteira(regras(max_posicoes=5, exposicao_maxima=10))
        assert c.abrir("a", "BTC", "e1", 1, em(0), 100, 96) is not None
        assert c.abrir("b", "BTC", "e2", 1, em(0), 100, 96) is None
        assert c.recusas[-1].motivo == "ja posicionado no par"

    def test_teto_de_exposicao(self):
        c = Carteira(regras(max_posicoes=5, max_por_par=5))
        c.abrir("a", "BTC", "e", 1, em(0), 100, 98)   # 100 = toda a banca
        assert c.abrir("b", "ETH", "e", 1, em(0), 100, 98) is None
        assert c.recusas[-1].motivo == "teto de exposicao"

    def test_abaixo_da_ordem_minima(self):
        c = Carteira(regras(valor_minimo_ordem=50.0))
        # Stop a 8%: posicao de 25, abaixo do minimo de 50.
        assert c.abrir("a", "BTC", "e", 1, em(0), 100, 92) is None
        assert c.recusas[-1].motivo == "abaixo da ordem minima"


class TestKillSwitch:
    def test_para_de_abrir_depois_da_perda_diaria(self):
        c = Carteira(regras(perda_diaria_maxima=0.05, max_posicoes=9, max_por_par=9))
        c.abrir("a", "BTC", "e", 1, em(0), 100, 96)   # valor 50
        c.fechar("a", em(1), -0.12)                    # perde 6 = 6% > 5%
        assert c.abrir("b", "ETH", "e", 1, em(2), 100, 96) is None
        assert c.recusas[-1].motivo == "perda diaria maxima"

    def test_volta_a_operar_no_dia_seguinte(self):
        c = Carteira(regras(perda_diaria_maxima=0.05, max_posicoes=9, max_por_par=9))
        c.abrir("a", "BTC", "e", 1, em(0), 100, 96)
        c.fechar("a", em(1), -0.12)
        assert c.abrir("b", "ETH", "e", 1, em(25), 100, 96) is not None


class TestPausa:
    def test_quatro_perdas_seguidas_pulam_os_proximos_sinais(self):
        c = Carteira(regras(perdas_seguidas_para_pausa=4, sinais_de_pausa=2,
                            perda_diaria_maxima=0, max_posicoes=9, max_por_par=9))
        for i in range(4):
            c.abrir(i, f"P{i}", "e", 1, em(i), 100, 99)
            c.fechar(i, em(i + 0.5), -0.01)
        assert c.abrir("x", "A", "e", 1, em(10), 100, 99) is None
        assert c.recusas[-1].motivo == "pausa apos sequencia de perdas"
        assert c.abrir("y", "B", "e", 1, em(11), 100, 99) is None
        assert c.abrir("z", "C", "e", 1, em(12), 100, 99) is not None, "pausa acabou"

    def test_um_ganho_zera_a_sequencia(self):
        c = Carteira(regras(perdas_seguidas_para_pausa=3, perda_diaria_maxima=0,
                            max_posicoes=9, max_por_par=9))
        for i in range(2):
            c.abrir(i, f"P{i}", "e", 1, em(i), 100, 99); c.fechar(i, em(i + 0.5), -0.01)
        c.abrir("g", "G", "e", 1, em(5), 100, 99); c.fechar("g", em(5.5), +0.02)
        assert c.perdas_seguidas == 0


def trades(*linhas) -> pd.DataFrame:
    """(par, estrategia, entrada_h, saida_h, retorno) -> quadro no formato do motor."""
    return pd.DataFrame(
        [
            {
                "par": p, "estrategia": e, "direcao": 1, "entrada": em(a), "saida": em(b),
                "preco_entrada": 100.0, "stop": 98.0, "retorno_liquido_pct": r, "motivo_saida": "ALVO",
            }
            for p, e, a, b, r in linhas
        ]
    )


class TestSimulacao:
    def test_posicoes_simultaneas_compartilham_a_banca(self):
        # Tres sinais na mesma hora, stop a 2%: cada um pediria 100% da banca.
        # Com o teto de exposicao, so o primeiro entra.
        quadro = trades(("A", "e", 0, 5, 0.05), ("B", "e", 0, 5, 0.05), ("C", "e", 0, 5, 0.05))
        resultado = simular_carteira(quadro, regras(max_posicoes=9, max_por_par=9))
        assert resultado.executados == 1
        assert resultado.recusas["teto de exposicao"] == 2

    def test_fechamento_libera_capital_antes_da_abertura_no_mesmo_instante(self):
        quadro = trades(("A", "e", 0, 5, 0.05), ("B", "e", 5, 9, 0.05))
        resultado = simular_carteira(quadro, regras(max_posicoes=9, max_por_par=9))
        assert resultado.executados == 2, "B abre no instante em que A fecha"

    def test_saldo_final_compoe_os_resultados(self):
        quadro = trades(("A", "e", 0, 5, 0.05), ("A", "e", 6, 9, -0.02))
        resultado = simular_carteira(quadro, regras())
        # 100 -> 105 (posicao de 100 a +5%) -> posicao de 105 a -2% = 102,9
        assert resultado.saldo_final == pytest.approx(102.9)
        assert resultado.executados == 2

    def test_trade_aberto_no_fim_dos_dados_e_ignorado(self):
        quadro = trades(("A", "e", 0, 5, 0.05))
        quadro.loc[0, "motivo_saida"] = "FIM_DADOS"
        assert simular_carteira(quadro, regras()).executados == 0
