"""Hora de quem le. Por dentro tudo e UTC; o que a pessoa ve e no fuso dela.

As velas chegam da corretora em UTC, e as regras que dependem de "que dia e"
(perda diaria, inicio e fim da campanha) precisam dar o mesmo resultado na
nuvem e na maquina de quem roda. Por isso nenhum calculo usa o relogio do
sistema operacional: o fuso e explicito, e a conversao acontece num lugar so.

O fuso vem de FUSO_HORARIO (padrao America/Sao_Paulo). O Brasil nao tem
horario de verao desde 2019, mas usar o banco de fusos em vez de "-3 fixo"
nao custa nada e nao quebra se ele voltar.
"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd

FUSO_PADRAO = "America/Sao_Paulo"
ROTULOS = {"America/Sao_Paulo": "horario de Brasilia", "UTC": "UTC"}


def fuso(nome: str | None = None) -> ZoneInfo:
    """O fuso pedido, o da variavel de ambiente, ou Brasilia."""
    return ZoneInfo(nome or os.environ.get("FUSO_HORARIO", FUSO_PADRAO))


def rotulo(z: ZoneInfo | None = None) -> str:
    z = z or fuso()
    return ROTULOS.get(z.key, z.key)


def local(momento, z: ZoneInfo | None = None):
    """Converte um instante para o fuso. Sem fuso no valor, assume UTC."""
    z = z or fuso()
    if isinstance(momento, pd.Timestamp):
        if momento.tzinfo is None:
            momento = momento.tz_localize("UTC")
        return momento.tz_convert(z)
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=timezone.utc)
    return momento.astimezone(z)


def dia(momento, z: ZoneInfo | None = None) -> date:
    """Data civil no fuso: e assim que 'o dia virou' deve ser contado."""
    return local(momento, z).date()


def formatar(momento, formato: str = "%d/%m %H:%M", z: ZoneInfo | None = None) -> str:
    return f"{local(momento, z):{formato}}"


def inicio_do_dia(texto: str, z: ZoneInfo | None = None) -> datetime:
    """'AAAA-MM-DD' digitado no fuso de quem digita, devolvido como instante UTC."""
    z = z or fuso()
    return datetime.fromisoformat(texto).replace(tzinfo=z).astimezone(timezone.utc)


def fim_do_dia(texto: str, z: ZoneInfo | None = None) -> datetime:
    """A meia-noite seguinte, no fuso, em UTC: 'ate sexta' inclui a sexta inteira.

    A soma de um dia e feita no fuso (hora de parede), nao em UTC, para
    continuar certa num dia em que o relogio pule por horario de verao.
    """
    z = z or fuso()
    proximo = datetime.fromisoformat(texto).replace(tzinfo=z) + timedelta(days=1)
    return proximo.astimezone(timezone.utc)


def deslocamento(momento, z: ZoneInfo | None = None) -> str:
    """Frase curta sobre a diferenca para o UTC, para o rodape dos relatorios."""
    horas = -local(momento, z).utcoffset().total_seconds() / 3600
    if horas == 0:
        return "igual ao UTC"
    lado = "a frente" if horas > 0 else "atras"
    return f"o UTC da corretora esta {abs(horas):g} horas {lado}"


def quadro_no_fuso(quadro: pd.DataFrame, z: ZoneInfo | None = None) -> pd.DataFrame:
    """Copia do quadro com toda coluna de data/hora convertida para o fuso."""
    z = z or fuso()
    saida = quadro.copy()
    for coluna in saida.columns:
        if pd.api.types.is_datetime64_any_dtype(saida[coluna]):
            serie = saida[coluna]
            if serie.dt.tz is None:
                serie = serie.dt.tz_localize("UTC")
            saida[coluna] = serie.dt.tz_convert(z)
    return saida
