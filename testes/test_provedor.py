"""Provedor de dados: paginacao, guardas de laco e bloqueio geografico."""
from __future__ import annotations

import ccxt
import pytest

from nucleo.dados.provedor_ccxt import (
    BloqueioGeografico,
    FalhaNaFonte,
    ProvedorCCXT,
)


class ClienteFalso:
    """Substitui o cliente do CCXT para testar o provedor sem rede."""

    def __init__(self, erro=None, velas=None, timeframes=None):
        self.erro = erro
        self.velas = velas or []
        self.timeframes = timeframes or {"1m": "1m", "5m": "5m", "1h": "1h"}
        self.chamadas = 0

    def fetch_ohlcv(self, par, timeframe=None, since=None, limit=None):
        self.chamadas += 1
        if self.erro is not None:
            raise self.erro
        return self.velas


class TestBloqueioGeografico:
    """451 e recusa por regiao: insistir nao muda nada."""

    def test_falha_na_primeira_tentativa(self):
        erro = ccxt.ExchangeNotAvailable(
            "okx GET https://api.exemplo.com/info 451 "
            '{"msg": "Service unavailable from a restricted location"}'
        )
        cliente = ClienteFalso(erro=erro)
        provedor = ProvedorCCXT("okx", cliente=cliente)

        with pytest.raises(BloqueioGeografico, match="451"):
            provedor.obter_velas("BTC/USDT", "1m", inicio=1_700_000_000_000)

        assert cliente.chamadas == 1, (
            "bloqueio geografico nao pode gastar as 4 tentativas: sao 15 "
            "segundos por par, e o resultado e o mesmo"
        )

    def test_mensagem_diz_o_que_fazer(self):
        erro = ccxt.ExchangeNotAvailable("binance 451 restricted location")
        provedor = ProvedorCCXT("binance", cliente=ClienteFalso(erro=erro))

        with pytest.raises(BloqueioGeografico) as capturado:
            provedor.obter_velas("BTC/USDT", "1m", inicio=1_700_000_000_000)

        mensagem = str(capturado.value)
        assert "EXCHANGE" in mensagem, "precisa dizer qual variavel mexer"
        assert "okx" in mensagem, "precisa sugerir alternativa concreta"

    def test_indisponibilidade_comum_ainda_tenta_de_novo(self):
        """Um 503 passageiro e diferente de um bloqueio por regiao."""
        cliente = ClienteFalso(erro=ccxt.ExchangeNotAvailable("okx 503 em manutencao"))
        provedor = ProvedorCCXT("okx", cliente=cliente)

        with pytest.raises(FalhaNaFonte):
            provedor.obter_velas("BTC/USDT", "1m", inicio=1_700_000_000_000)

        assert cliente.chamadas > 1, "erro passageiro merece nova tentativa"


class TestTimeframes:
    def test_recusa_timeframe_que_a_corretora_nao_tem(self):
        provedor = ProvedorCCXT("okx", cliente=ClienteFalso())
        with pytest.raises(ValueError, match="nao oferece"):
            provedor.obter_velas("BTC/USDT", "4h", inicio=1_700_000_000_000)


class ClientePaginado:
    """Corretora que entrega no maximo `por_pagina` velas por chamada."""

    def __init__(self, inicio_ms: int, total: int, passo_ms: int, por_pagina: int):
        self.velas = [
            [inicio_ms + i * passo_ms, 1.0, 2.0, 0.5, 1.5, 1.0] for i in range(total)
        ]
        self.por_pagina = por_pagina
        self.timeframes = {"1m": "1m", "4h": "4h"}
        self.chamadas = 0

    def fetch_ohlcv(self, par, timeframe=None, since=None, limit=None):
        self.chamadas += 1
        a_partir = [v for v in self.velas if v[0] >= (since or 0)]
        return a_partir[: self.por_pagina]


class TestPaginacao:
    def test_baixa_tudo_mesmo_quando_a_corretora_entrega_menos_por_pagina(self):
        """Regressao do buraco de dois anos na OKX.

        Pedimos 1000 velas por pagina; a OKX entrega 300. O teto de paginas era
        calculado a partir do que PEDIMOS, entao o laco parava cedo e devolvia
        um pedaco do historico como se fosse tudo.
        """
        passo = 4 * 3600 * 1000
        inicio = 1_640_000_000_000
        total = 5_000  # 5000 velas de 4h = ~2,3 anos
        cliente = ClientePaginado(inicio, total, passo, por_pagina=300)
        provedor = ProvedorCCXT("okx", cliente=cliente, limite_por_pagina=1000)

        quadro = provedor.obter_velas(
            "BTC/USDT", "4h", inicio=inicio, fim=inicio + (total + 5) * passo
        )
        assert len(quadro) == total, f"veio {len(quadro)} de {total}: paginacao truncou"
        assert cliente.chamadas >= total // 300

    def test_para_quando_a_corretora_ignora_o_cursor(self):
        """Guarda contra laco infinito.

        Algumas corretoras ignoram o `since` e devolvem sempre o mesmo bloco.
        Sem essa guarda a paginacao nunca terminaria.
        """
        velas = [[1_700_000_000_000, 1.0, 2.0, 0.5, 1.5, 10.0]]
        cliente = ClienteFalso(velas=velas)
        provedor = ProvedorCCXT("okx", cliente=cliente)

        quadro = provedor.obter_velas(
            "BTC/USDT", "1m",
            inicio=1_700_000_000_000,
            fim=1_700_000_000_000 + 60_000 * 5_000,
        )
        assert cliente.chamadas < 10, "deveria desistir cedo, nao girar em falso"
        assert len(quadro) <= 1
