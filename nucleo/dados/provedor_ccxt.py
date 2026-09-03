"""Fonte de velas via CCXT.

CCXT fala com mais de cem corretoras pela mesma interface, entao a corretora
vira configuracao em vez de codigo. Isso responde diretamente ao que matou a
versao anterior deste projeto: ele foi escrito em cima da API da NovaDAX, a
corretora encerrou as operacoes no Brasil e o host da API saiu do ar. Aqui,
trocar de fonte e mudar uma linha do .env.
"""
from __future__ import annotations

import os
import time
from datetime import datetime

import ccxt
import pandas as pd

from .provedor import (
    COLUNAS_VELA,
    ProvedorDados,
    agora_ms,
    descartar_vela_aberta,
    duracao_ms,
    normalizar,
    para_ms,
    quadro_vazio,
    validar_velas,
)

LIMITE_PADRAO = 1000
MAX_TENTATIVAS = 4
VAZIOS_ATE_DESISTIR = 3
JANELA_PADRAO_MS = 365 * 24 * 60 * 60 * 1000


class CorretoraDesconhecida(ValueError):
    """Nome de corretora que o CCXT nao reconhece."""


class FalhaNaFonte(RuntimeError):
    """A corretora nao respondeu depois de todas as tentativas."""


class ProvedorCCXT(ProvedorDados):
    """Velas e precos de qualquer corretora suportada pelo CCXT."""

    def __init__(
        self,
        corretora: str | None = None,
        limite_por_pagina: int = LIMITE_PADRAO,
        cliente=None,
    ) -> None:
        self.nome = (corretora or os.getenv("EXCHANGE", "binance")).strip().lower()
        self.limite_por_pagina = limite_por_pagina

        if cliente is not None:
            self.cliente = cliente
            return

        classe = getattr(ccxt, self.nome, None)
        if classe is None:
            raise CorretoraDesconhecida(
                f"O CCXT nao conhece a corretora {self.nome!r}. "
                f"Exemplos validos: binance, bybit, okx, kraken, mexc, gateio."
            )
        # enableRateLimit faz o proprio CCXT espacar as chamadas conforme o
        # limite publicado da corretora - e o que evita tomar 429 na paginacao.
        self.cliente = classe({"enableRateLimit": True, "timeout": 30_000})

    def timeframes_nativos(self) -> frozenset[str]:
        return frozenset(self.cliente.timeframes or {})

    def obter_preco(self, par: str) -> float:
        cotacao = self.cliente.fetch_ticker(par)
        ultimo = cotacao.get("last") or cotacao.get("close")
        if ultimo is None:
            raise FalhaNaFonte(f"{self.nome} nao devolveu preco para {par}.")
        return float(ultimo)

    def obter_velas(
        self,
        par: str,
        timeframe: str,
        inicio: datetime | str | int | None = None,
        fim: datetime | str | int | None = None,
    ) -> pd.DataFrame:
        passo = duracao_ms(timeframe)

        nativos = self.timeframes_nativos()
        if nativos and timeframe not in nativos:
            raise ValueError(
                f"{self.nome} nao oferece {timeframe} nativamente. Baixe um "
                f"timeframe menor e use reamostrar(). Disponiveis: {sorted(nativos)}"
            )

        fim_ms = para_ms(fim) or agora_ms()
        inicio_ms = para_ms(inicio)
        if inicio_ms is None:
            inicio_ms = fim_ms - JANELA_PADRAO_MS
        if inicio_ms >= fim_ms:
            return quadro_vazio()

        linhas = self._paginar(par, timeframe, inicio_ms, fim_ms, passo)
        if not linhas:
            return quadro_vazio()

        quadro = self._montar_quadro(linhas)
        quadro = descartar_vela_aberta(quadro, timeframe)
        quadro = quadro[
            (quadro.index >= pd.Timestamp(inicio_ms, unit="ms", tz="UTC"))
            & (quadro.index <= pd.Timestamp(fim_ms, unit="ms", tz="UTC"))
        ]
        validar_velas(quadro, f"{self.nome} {par} {timeframe}")
        return quadro

    def _paginar(
        self, par: str, timeframe: str, inicio_ms: int, fim_ms: int, passo: int
    ) -> list[list]:
        """Percorre o historico em blocos ate cobrir o intervalo pedido.

        Toda corretora limita quantas velas devolve por chamada - a Binance da
        1000 -, entao um intervalo longo vira varias requisicoes encadeadas.
        """
        salto_vazio = passo * self.limite_por_pagina
        teto_paginas = (fim_ms - inicio_ms) // salto_vazio + VAZIOS_ATE_DESISTIR + 5

        linhas: list[list] = []
        cursor = inicio_ms
        vazias = 0
        paginas = 0

        while cursor <= fim_ms and paginas < teto_paginas:
            paginas += 1
            lote = self._buscar_pagina(par, timeframe, cursor)

            if not lote:
                # Pode ser fim do historico ou um buraco de indisponibilidade.
                # Pula a janela e desiste depois de algumas tentativas secas.
                vazias += 1
                if vazias >= VAZIOS_ATE_DESISTIR:
                    break
                cursor += salto_vazio
                continue

            vazias = 0
            linhas.extend(lote)

            ultimo = int(lote[-1][0])
            if ultimo < cursor:
                # A corretora ignorou o `since` e devolveu o mesmo bloco de novo.
                # Sem essa guarda o laco nunca termina.
                break
            cursor = ultimo + passo

        return linhas

    def _buscar_pagina(self, par: str, timeframe: str, desde: int) -> list[list]:
        ultimo_erro: Exception | None = None
        for tentativa in range(MAX_TENTATIVAS):
            try:
                return self.cliente.fetch_ohlcv(
                    par, timeframe=timeframe, since=desde, limit=self.limite_por_pagina
                )
            except ccxt.RateLimitExceeded as erro:
                ultimo_erro = erro
                time.sleep(2**tentativa * 2)
            except ccxt.NetworkError as erro:
                ultimo_erro = erro
                time.sleep(2**tentativa)
        raise FalhaNaFonte(
            f"{self.nome} nao respondeu para {par} {timeframe} depois de "
            f"{MAX_TENTATIVAS} tentativas."
        ) from ultimo_erro

    @staticmethod
    def _montar_quadro(linhas: list[list]) -> pd.DataFrame:
        if any(len(linha) < 6 for linha in linhas[:5]):
            raise FalhaNaFonte(
                "A corretora devolveu velas fora do formato esperado "
                "[tempo, abertura, maxima, minima, fechamento, volume]."
            )

        quadro = pd.DataFrame(
            [linha[:6] for linha in linhas], columns=["tempo", *COLUNAS_VELA]
        )
        quadro.index = pd.to_datetime(quadro.pop("tempo"), unit="ms", utc=True)
        return normalizar(quadro)
