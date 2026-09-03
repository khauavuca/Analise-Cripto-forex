"""Contrato das estrategias e a composicao por confluencia."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from nucleo.estrategias.base import (
    COMPRA,
    NEUTRO,
    VENDA,
    Estrategia,
    apenas_transicoes,
    quadro_sinais,
    validar_sinais,
)
from nucleo.estrategias.composta import EstrategiaComposta
from nucleo.estrategias.cruzamento_ema import EstrategiaCruzamentoEma
from nucleo.estrategias.rsi_macd import EstrategiaRsiMacd, ParametrosRsiMacd


@pytest.fixture
def quadro() -> pd.DataFrame:
    gerador = np.random.default_rng(11)
    fechamento = 100 + gerador.normal(0, 1.5, 900).cumsum()
    amplitude = np.abs(gerador.normal(0, 0.8, 900)) + 0.1
    indice = pd.date_range("2024-01-01", periods=900, freq="4h", tz="UTC")
    return pd.DataFrame(
        {
            "abertura": np.r_[fechamento[0], fechamento[:-1]],
            "maxima": fechamento + amplitude,
            "minima": fechamento - amplitude,
            "fechamento": fechamento,
            "volume": np.ones(900),
        },
        index=indice,
    )


class EstrategiaFixa(Estrategia):
    """Componente de teste: opina sempre na mesma direcao."""

    def __init__(self, direcao: int, forca: float = 0.8, distancia: float = 0.05):
        self.direcao = direcao
        self.forca = forca
        self.distancia = distancia
        self.nome = f"fixa({direcao})"

    def barras_de_aquecimento(self) -> int:
        return 1

    def gerar_sinais(self, quadro: pd.DataFrame) -> pd.DataFrame:
        sinais = quadro_sinais(quadro.index)
        sinais["direcao"] = np.int8(self.direcao)
        sinais["forca"] = self.forca
        fechamento = quadro["fechamento"]
        sinais["stop"] = fechamento * (1 - self.direcao * self.distancia)
        sinais["alvo"] = fechamento * (1 + self.direcao * self.distancia * 2)
        sinais["motivo"] = "fixa"
        return sinais


class TestContrato:
    def test_indice_de_saida_e_igual_ao_de_entrada(self, quadro):
        for estrategia in (EstrategiaRsiMacd(), EstrategiaCruzamentoEma()):
            sinais = estrategia.gerar_sinais(quadro)
            assert sinais.index.equals(quadro.index)

    def test_validar_recusa_indice_diferente(self, quadro):
        sinais = quadro_sinais(quadro.index).iloc[10:]
        with pytest.raises(ValueError, match="indice diferente"):
            validar_sinais(sinais, quadro.index)

    def test_aquecimento_nao_gera_sinal(self, quadro):
        estrategia = EstrategiaRsiMacd()
        sinais = estrategia.gerar_sinais(quadro)
        aquecimento = sinais.iloc[: estrategia.p.sma_longa - 1]
        assert (aquecimento["direcao"] == NEUTRO).all()


class TestModoEvento:
    def test_transicoes_mantem_so_a_virada(self):
        estado = pd.Series([0, 1, 1, 1, 0, -1, -1, 0], dtype="int8")
        assert apenas_transicoes(estado).tolist() == [0, 1, 0, 0, 0, -1, 0, 0]

    def test_evento_e_subconjunto_de_estado(self, quadro):
        """Evento nunca inventa sinal: ele so remove as repeticoes de estado."""
        por_estado = EstrategiaRsiMacd(ParametrosRsiMacd(modo="estado")).gerar_sinais(quadro)
        por_evento = EstrategiaRsiMacd(ParametrosRsiMacd(modo="evento")).gerar_sinais(quadro)

        disparou = por_evento.direcao != 0
        assert disparou.sum() > 0, "o cenario precisa de ao menos um sinal"
        assert disparou.sum() <= (por_estado.direcao != 0).sum()
        # Onde o evento dispara, o estado concorda na direcao.
        assert (por_evento.direcao[disparou] == por_estado.direcao[disparou]).all()


class TestComposta:
    def test_componentes_concordando_geram_sinal(self, quadro):
        """Regressao: o concat com nomes de coluna repetidos fazia o `where`
        alinhar por nome, falhar e devolver stop/alvo NaN em toda linha - o
        que descartava 100% dos sinais em silencio."""
        composta = EstrategiaComposta(
            [EstrategiaFixa(COMPRA), EstrategiaFixa(COMPRA)], limiar=0.4
        )
        sinais = composta.gerar_sinais(quadro)

        assert (sinais.direcao == COMPRA).sum() == 1, "converte estado em evento"
        emitido = sinais[sinais.direcao == COMPRA].iloc[0]
        assert np.isfinite(emitido.stop), "stop nao pode sair NaN"
        assert np.isfinite(emitido.alvo)
        assert emitido.stop < quadro.fechamento.iloc[0] < emitido.alvo

    def test_componentes_discordando_nao_geram_sinal(self, quadro):
        composta = EstrategiaComposta(
            [EstrategiaFixa(COMPRA), EstrategiaFixa(VENDA)], limiar=0.4
        )
        assert (composta.gerar_sinais(quadro).direcao != NEUTRO).sum() == 0

    def test_unanime_exige_todos(self, quadro):
        parcial = EstrategiaComposta(
            [EstrategiaFixa(COMPRA), EstrategiaFixa(NEUTRO, forca=0.0)], modo="unanime"
        )
        assert (parcial.gerar_sinais(quadro).direcao != NEUTRO).sum() == 0

    def test_protecao_escolhe_a_mais_conservadora(self, quadro):
        """Nunca a media: a media entre dois stops pode cair do lado errado."""
        composta = EstrategiaComposta(
            [EstrategiaFixa(COMPRA, distancia=0.10), EstrategiaFixa(COMPRA, distancia=0.02)],
            limiar=0.4,
        )
        emitido = composta.gerar_sinais(quadro)
        linha = emitido[emitido.direcao == COMPRA].iloc[0]
        fechamento = quadro.fechamento.iloc[0]

        # Comprado: o stop mais proximo e o mais alto (2% abaixo, nao 10%).
        assert linha.stop == pytest.approx(fechamento * 0.98)
        # E o alvo mais proximo e o mais baixo (4% acima, nao 20%).
        assert linha.alvo == pytest.approx(fechamento * 1.04)

    def test_converte_componentes_para_modo_estado(self):
        composta = EstrategiaComposta([EstrategiaRsiMacd(ParametrosRsiMacd(modo="evento"))])
        assert composta.componentes[0][0].p.modo == "estado"
