"""Indicadores tecnicos como funcoes puras.

Duas regras que valem para tudo aqui:

1. **Serie inteira entra, serie inteira sai.** Nunca "o ultimo valor". O codigo
   antigo devolvia `float(x.iloc[-1])`, uma API escalar que obriga o backtest a
   ou recalcular em laco ou reimplementar - e ai backtest e producao divergem
   sem ninguem notar. Aqui o mesmo vetor serve os dois: ao vivo e so ler a
   ultima linha do resultado.
2. **Nada de olhar o futuro.** O valor da posicao `i` depende apenas das
   posicoes `0..i`. `testes/test_indicadores.py` verifica isso cortando a serie
   e comparando.

Implementado a mao de proposito: `pandas-ta` nao importa com numpy 2 (usa
`numpy.NaN`, removido) e nao ha wheel de TA-Lib para cp314 no Windows. Alem
disso, num projeto cuja meta e medir assertividade, e preciso saber
exatamente o que cada numero e.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def media_movel_simples(serie: pd.Series, periodo: int) -> pd.Series:
    return serie.rolling(periodo, min_periods=periodo).mean()


def media_movel_exponencial(serie: pd.Series, periodo: int) -> pd.Series:
    return serie.ewm(span=periodo, adjust=False, min_periods=periodo).mean()


def suavizacao_wilder(serie: pd.Series, periodo: int) -> pd.Series:
    """Media suavizada de Wilder: EMA com alfa = 1/periodo.

    E o que RSI, ATR e ADX usam de verdade. O codigo antigo usava
    `rolling().mean()` (media simples, tambem chamada de Cutler): os dois
    calculos divergem em varios pontos e discordam com frequencia sobre
    cruzar 30 ou 70 - ou seja, viram estrategias diferentes.

    `min_periods` importa: com `adjust=False` o pandas emitiria um valor ja na
    primeira barra, semeado por uma unica observacao. O backtest operaria
    aquilo como se fosse indicador.
    """
    return serie.ewm(alpha=1 / periodo, adjust=False, min_periods=periodo).mean()


def indice_forca_relativa(fechamento: pd.Series, periodo: int = 14) -> pd.Series:
    """RSI de Wilder, de 0 a 100."""
    variacao = fechamento.diff()
    ganho = variacao.clip(lower=0)
    perda = -variacao.clip(upper=0)

    media_ganho = suavizacao_wilder(ganho, periodo)
    media_perda = suavizacao_wilder(perda, periodo)

    # Sem epsilon no denominador. Num par cotado a 80.000 um 1e-10 e ruido
    # inofensivo, mas num par cotado a 0,00001 ele dominaria a conta. Os casos
    # degenerados sao tratados na mao, que e mais honesto que encobrir.
    with np.errstate(divide="ignore", invalid="ignore"):
        forca = media_ganho / media_perda
    resultado = 100 - 100 / (1 + forca)

    valido = media_ganho.notna() & media_perda.notna()
    so_alta = valido & (media_perda == 0) & (media_ganho > 0)
    parado = valido & (media_perda == 0) & (media_ganho == 0)

    return resultado.mask(so_alta, 100.0).mask(parado, 50.0).where(valido)


def macd(
    fechamento: pd.Series, rapida: int = 12, lenta: int = 26, sinal: int = 9
) -> pd.DataFrame:
    """Convergencia/divergencia de medias. Colunas: macd, sinal, histograma."""
    linha = media_movel_exponencial(fechamento, rapida) - media_movel_exponencial(
        fechamento, lenta
    )
    linha_sinal = linha.ewm(span=sinal, adjust=False, min_periods=sinal).mean()
    return pd.DataFrame(
        {"macd": linha, "sinal": linha_sinal, "histograma": linha - linha_sinal}
    )


def faixa_verdadeira(
    maxima: pd.Series, minima: pd.Series, fechamento: pd.Series
) -> pd.Series:
    """True Range: o maior entre amplitude da vela e os gaps contra o fechamento anterior."""
    anterior = fechamento.shift(1)
    faixas = pd.concat(
        [maxima - minima, (maxima - anterior).abs(), (minima - anterior).abs()], axis=1
    )
    resultado = faixas.max(axis=1)
    # Na primeira barra nao existe fechamento anterior; a amplitude e o que ha.
    resultado.iloc[0] = maxima.iloc[0] - minima.iloc[0]
    return resultado


def faixa_verdadeira_media(
    maxima: pd.Series, minima: pd.Series, fechamento: pd.Series, periodo: int = 14
) -> pd.Series:
    """ATR de Wilder - a medida de volatilidade que dimensiona stop e alvo."""
    return suavizacao_wilder(faixa_verdadeira(maxima, minima, fechamento), periodo)


def bandas_bollinger(
    fechamento: pd.Series, periodo: int = 20, desvios: float = 2.0
) -> pd.DataFrame:
    """Colunas: inferior, media, superior, largura."""
    media = media_movel_simples(fechamento, periodo)
    desvio = fechamento.rolling(periodo, min_periods=periodo).std(ddof=0)
    superior = media + desvios * desvio
    inferior = media - desvios * desvio
    return pd.DataFrame(
        {
            "inferior": inferior,
            "media": media,
            "superior": superior,
            "largura": (superior - inferior) / media,
        }
    )


def indice_direcional_medio(
    maxima: pd.Series, minima: pd.Series, fechamento: pd.Series, periodo: int = 14
) -> pd.DataFrame:
    """ADX de Wilder. Colunas: adx, di_mais, di_menos.

    Mede forca de tendencia sem dizer a direcao. Serve de filtro: estrategia de
    reversao a media costuma apanhar quando o ADX esta alto.
    """
    subiu = maxima.diff()
    caiu = -minima.diff()

    movimento_alta = subiu.where((subiu > caiu) & (subiu > 0), 0.0)
    movimento_baixa = caiu.where((caiu > subiu) & (caiu > 0), 0.0)

    amplitude = suavizacao_wilder(faixa_verdadeira(maxima, minima, fechamento), periodo)

    with np.errstate(divide="ignore", invalid="ignore"):
        di_mais = 100 * suavizacao_wilder(movimento_alta, periodo) / amplitude
        di_menos = 100 * suavizacao_wilder(movimento_baixa, periodo) / amplitude
        soma = di_mais + di_menos
        indice = 100 * (di_mais - di_menos).abs() / soma

    indice = indice.where(soma != 0, 0.0)
    return pd.DataFrame(
        {"adx": suavizacao_wilder(indice, periodo), "di_mais": di_mais, "di_menos": di_menos}
    )


def canal_donchian(
    maxima: pd.Series, minima: pd.Series, periodo: int = 50, deslocar: bool = True
) -> pd.DataFrame:
    """Maior maxima e menor minima da janela. Colunas: resistencia, suporte.

    `deslocar=True` exclui a barra corrente da janela, e esse e o padrao por um
    motivo: o codigo antigo fazia `tail(50)["maxima"].max()` incluindo a propria
    vela em analise. Usar isso como nivel de entrada e olhar o resultado da
    barra para decidir se entra nela.
    """
    resistencia = maxima.rolling(periodo, min_periods=periodo).max()
    suporte = minima.rolling(periodo, min_periods=periodo).min()
    if deslocar:
        resistencia = resistencia.shift(1)
        suporte = suporte.shift(1)
    return pd.DataFrame({"resistencia": resistencia, "suporte": suporte})
