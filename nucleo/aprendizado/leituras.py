"""Leituras de grafico: a literatura de trading traduzida em colunas, com procedencia.

Cada leitura e uma frase de livro ("topo e fundo ascendentes e estrutura de
alta") escrita como funcao das cinco coisas que o grafico entrega - abertura,
maxima, minima, fechamento, volume - e da hora. O modelo de ML nao le livro;
le estas colunas. E aprende, nos dados, QUANTO cada leitura merece de
confianca para cada setup - inclusive nada.

Tres regras que toda leitura obedece:

**Causal.** A leitura na barra `t` usa so barras ate `t`, inclusive. Topo e
fundo so entram depois de confirmados; a tendencia do grafico maior so usa
velas maiores ja fechadas. `test_leituras.py` prova isso anexando futuro e
conferindo que o passado nao muda.

**Relativa a direcao.** "Estrutura de alta" ajuda compra e atrapalha venda.
Leituras direcionais viram `*_a_favor` (+1 a favor, -1 contra, 0 neutra) no
instante do sinal. Sobre algumas delas o modelo recebe uma restricao de
monotonia: a favor nunca pode diminuir a probabilidade. E o "respeitar o
grafico" escrito em codigo - o modelo escolhe o peso, nunca inverte a logica.
A restricao so vale para o que e universal (tendencia nas varias escalas e
concordancia entre setups); momento de curto prazo fica livre, porque um
setup de reversao entra justamente contra ele.

**Adimensional.** Distancias em fracao do preco, nunca em dolar, para BTC e
XRP caberem no mesmo modelo.

O catalogo diz, para cada leitura, a regra, a fonte e se e direcional. Fonte
nao valida leitura - so o walk-forward valida. Fonte serve para explicar.
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..dados.provedor import duracao_ms, reamostrar
from ..indicadores.basicos import (
    bandas_bollinger,
    indice_direcional_medio,
    indice_forca_relativa,
    media_movel_exponencial,
    pivos,
)


@dataclass(frozen=True)
class Leitura:
    nome: str
    familia: str
    regra: str
    fonte: str
    direcional: bool  # multiplica pela direcao do sinal e vira `<nome>_a_favor`
    monotona: bool  # a favor so pode ajudar: restricao imposta ao modelo


CATALOGO: tuple[Leitura, ...] = (
    # ------------------------------------------------------------ estrutura
    Leitura(
        "estrutura", "estrutura",
        "+1 se o ultimo topo e o ultimo fundo confirmados estao acima dos anteriores; "
        "-1 se ambos abaixo; 0 se misto. Pivos de 5 barras, so depois de confirmados.",
        "Teoria de Dow; Murphy, Technical Analysis of the Financial Markets (1999), cap. 4",
        True, True,
    ),
    Leitura(
        "dist_topo_pct", "estrutura",
        "distancia do fechamento ao ultimo topo confirmado, em fracao do preco (positivo = topo acima)",
        "Edwards & Magee, Technical Analysis of Stock Trends (1948)", False, False,
    ),
    Leitura(
        "dist_fundo_pct", "estrutura",
        "distancia do fechamento ao ultimo fundo confirmado, em fracao do preco (positivo = fundo abaixo)",
        "Edwards & Magee (1948)", False, False,
    ),
    Leitura(
        "perna_pct", "estrutura", "tamanho da ultima perna (topo - fundo) em fracao do preco",
        "Wyckoff, The Day Trader's Bible (1919); Brooks, Trading Price Action Trends (2012)", False, False,
    ),
    # ------------------------------------------------------------ tendencia
    Leitura(
        "tendencia_medias", "tendencia",
        "+1 fechamento acima da EMA50 e da EMA200; -1 abaixo das duas; 0 entre elas",
        "Murphy (1999), cap. 9; Elder, Come Into My Trading Room (2002)", True, True,
    ),
    Leitura(
        "inclinacao_ema50", "tendencia", "variacao da EMA50 nas ultimas 10 barras, em fracao do preco",
        "Elder (2002): a inclinacao da media e a tendencia", True, True,
    ),
    Leitura(
        "adx14", "tendencia", "ADX de 14: forca da tendencia, sem direcao (0 a 100)",
        "Wilder, New Concepts in Technical Trading Systems (1978)", False, False,
    ),
    Leitura(
        "tendencia_maior", "tendencia",
        "tendencia por medias (EMA20/EMA50) no grafico maior - 4h para 1h, diario para 4h - "
        "usando so velas maiores ja fechadas no instante",
        "Elder, Triple Screen (1986; 2002): opere a favor da mare", True, True,
    ),
    Leitura(
        "ret_maior_5", "tendencia", "retorno das ultimas 5 velas fechadas do grafico maior",
        "Elder, Triple Screen", True, True,
    ),
    # ------------------------------------------------------------ momento
    Leitura("rsi14", "momento", "RSI de 14 (0 a 100)", "Wilder (1978)", False, False),
    Leitura(
        "macd_hist_rel", "momento", "histograma do MACD (12/26/9) dividido pelo preco",
        "Appel, Technical Analysis: Power Tools (2005); Elder (2002)", True, False,
    ),
    Leitura(
        "momento_20", "momento", "retorno das ultimas 20 barras",
        "Jegadeesh & Titman (1993); Moskowitz, Ooi & Pedersen, Time Series Momentum (2012)", True, False,
    ),
    # ------------------------------------------------------------ volatilidade
    Leitura("atr_rel", "volatilidade", "ATR de 14 (Wilder) em fracao do preco", "Wilder (1978)", False, False),
    Leitura(
        "regime_vol", "volatilidade",
        "ATR relativo dividido pela propria media de 100 barras (>1 agitado, <1 calmo)",
        "Bollinger, Bollinger on Bollinger Bands (2001): volatilidade e ciclica", False, False,
    ),
    Leitura(
        "bb_largura", "volatilidade", "largura das bandas de Bollinger (20, 2 desvios) em fracao do preco",
        "Bollinger (2001)", False, False,
    ),
    Leitura(
        "squeeze", "volatilidade", "1 se as bandas de Bollinger estao dentro do canal de Keltner (20, 2 ATR)",
        "Carter, Mastering the Trade (2005): TTM Squeeze", False, False,
    ),
    # ------------------------------------------------------------ volume
    Leitura(
        "volume_rel_5", "volume", "volume da barra dividido pela media das 5 anteriores",
        "Murphy (1999), cap. 7: volume confirma o movimento", False, False,
    ),
    # ------------------------------------------------------------ localizacao
    Leitura(
        "posicao_range_50", "localizacao",
        "onde o fechamento esta no range das ultimas 50 barras (0 = na minima, 1 = na maxima)",
        "Williams %R (1973); Lane, estocastico (1950s)", False, False,
    ),
    Leitura(
        "dist_max_50_pct", "localizacao", "distancia ate a maxima de 50 barras, em fracao do preco",
        "Regras das Tartarugas (Dennis & Eckhardt, 1983): rompimento de 55 barras", False, False,
    ),
    Leitura(
        "dist_min_50_pct", "localizacao", "distancia ate a minima de 50 barras, em fracao do preco",
        "Regras das Tartarugas (1983)", False, False,
    ),
    # ------------------------------------------------------------ vela
    Leitura(
        "corpo_rel", "vela", "corpo da vela dividido pela amplitude (0 = doji, 1 = marubozu)",
        "Nison, Japanese Candlestick Charting Techniques (1991)", False, False,
    ),
    Leitura("vela", "vela", "+1 vela de alta, -1 de baixa, 0 doji", "Nison (1991)", True, False),
    Leitura(
        "pavio_contra_rel", "vela",
        "pavio do lado contrario ao fechamento, em fracao da amplitude (rejeicao: martelo, pin bar)",
        "Nison (1991)", False, False,
    ),
    Leitura(
        "sequencia", "vela", "quantas velas seguidas na mesma direcao da atual, com o sinal dela, ate 8",
        "Nison (1991); exaustao de sequencia", True, False,
    ),
)

# A confluencia nao vem de um so quadro: precisa dos sinais dos outros setups.
CONFLUENCIA = Leitura(
    "confluencia", "confluencia",
    "quantos OUTROS setups emitiram sinal na mesma direcao nas ultimas 6 barras, menos quantos "
    "emitiram na direcao contraria",
    "Pring, Technical Analysis Explained (1980): peso da evidencia", True, True,
)

DIRECIONAIS = frozenset(l.nome for l in CATALOGO if l.direcional) | {CONFLUENCIA.nome}
MONOTONAS = frozenset(f"{l.nome}_a_favor" for l in CATALOGO if l.monotona) | {f"{CONFLUENCIA.nome}_a_favor"}

TIMEFRAME_MAIOR = {"1m": "15m", "5m": "1h", "15m": "4h", "1h": "4h", "4h": "1d", "1d": "1w"}


def _ema(serie: pd.Series, periodo: int) -> pd.Series:
    return media_movel_exponencial(serie, periodo)


def _tres_estados(acima: pd.Series, abaixo: pd.Series, valido: pd.Series) -> pd.Series:
    saida = pd.Series(np.select([acima, abaixo], [1.0, -1.0], 0.0), index=acima.index)
    return saida.where(valido)


def _atr_wilder(maxima: pd.Series, minima: pd.Series, fechamento: pd.Series, periodo: int) -> pd.Series:
    anterior = fechamento.shift(1)
    faixa = pd.concat(
        [maxima - minima, (maxima - anterior).abs(), (minima - anterior).abs()], axis=1
    ).max(axis=1)
    return faixa.ewm(alpha=1 / periodo, adjust=False, min_periods=periodo).mean()


def _anterior_confirmado(preco: pd.Series, novo: pd.Series, indice: pd.Index) -> pd.Series:
    """Valor do pivo ANTERIOR ao ultimo confirmado, carregado para frente."""
    return preco.where(novo).dropna().shift(1).reindex(indice).ffill()


def _grafico_maior(quadro: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Tendencia e retorno do grafico maior, disponiveis so quando a vela maior fechou.

    A vela de 4h que contem a vela de 1h das 03:00 fecha as 04:00 - o mesmo
    instante em que a vela de 1h fecha. Ela pode ser usada nessa barra, e so
    nela em diante. `reamostrar` ja descarta o balde incompleto; aqui a
    informacao e alinhada pelo horario de FECHAMENTO da vela maior.
    """
    maior = TIMEFRAME_MAIOR.get(timeframe)
    vazio = pd.DataFrame({"tendencia_maior": np.nan, "ret_maior_5": np.nan}, index=quadro.index)
    if maior is None or len(quadro) < 2:
        return vazio
    try:
        # Para o diario o pandas avisa que `origin` nao se aplica a "1D"; o
        # alinhamento a meia-noite UTC e o padrao nesse caso, entao esta certo.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            q = reamostrar(quadro, timeframe, maior)
    except Exception:
        return vazio
    if len(q) < 30:
        return vazio

    f = q["fechamento"]
    e20, e50 = _ema(f, 20), _ema(f, 50)
    tendencia = _tres_estados((f > e20) & (f > e50), (f < e20) & (f < e50), e50.notna())
    retorno = f.pct_change(5)

    fechou_em = q.index + pd.Timedelta(milliseconds=duracao_ms(maior))
    disponivel = quadro.index + pd.Timedelta(milliseconds=duracao_ms(timeframe))
    alinhar = lambda s: pd.Series(s.to_numpy(), index=fechou_em).reindex(disponivel, method="ffill").set_axis(quadro.index)  # noqa: E731
    return pd.DataFrame({"tendencia_maior": alinhar(tendencia), "ret_maior_5": alinhar(retorno)})


def calcular(quadro: pd.DataFrame, timeframe: str | None = None) -> pd.DataFrame:
    """Todas as leituras do catalogo, barra a barra, so com o passado."""
    abertura, maxima, minima, fechamento, volume = (
        quadro[c] for c in ("abertura", "maxima", "minima", "fechamento", "volume")
    )
    x = pd.DataFrame(index=quadro.index)

    # ---- estrutura: topos e fundos confirmados, comparados com os anteriores
    p = pivos(maxima, minima, esquerda=5, direita=5)
    topo_ant = _anterior_confirmado(p["topo"], p["topo_novo"], quadro.index)
    fundo_ant = _anterior_confirmado(p["fundo"], p["fundo_novo"], quadro.index)
    valido = p["topo"].notna() & p["fundo"].notna() & topo_ant.notna() & fundo_ant.notna()
    x["estrutura"] = _tres_estados(
        (p["topo"] > topo_ant) & (p["fundo"] > fundo_ant),
        (p["topo"] < topo_ant) & (p["fundo"] < fundo_ant),
        valido,
    )
    x["dist_topo_pct"] = p["topo"] / fechamento - 1
    x["dist_fundo_pct"] = 1 - p["fundo"] / fechamento
    x["perna_pct"] = (p["topo"] - p["fundo"]).abs() / fechamento

    # ---- tendencia
    ema50, ema200 = _ema(fechamento, 50), _ema(fechamento, 200)
    x["tendencia_medias"] = _tres_estados(
        (fechamento > ema50) & (fechamento > ema200),
        (fechamento < ema50) & (fechamento < ema200),
        ema200.notna(),
    )
    x["inclinacao_ema50"] = (ema50 - ema50.shift(10)) / fechamento
    x["adx14"] = indice_direcional_medio(maxima, minima, fechamento, 14)["adx"]
    x = x.join(_grafico_maior(quadro, timeframe) if timeframe else pd.DataFrame(
        {"tendencia_maior": np.nan, "ret_maior_5": np.nan}, index=quadro.index
    ))

    # ---- momento
    x["rsi14"] = indice_forca_relativa(fechamento, 14)
    linha_macd = _ema(fechamento, 12) - _ema(fechamento, 26)
    x["macd_hist_rel"] = (linha_macd - _ema(linha_macd, 9)) / fechamento
    x["momento_20"] = fechamento.pct_change(20)

    # ---- volatilidade
    atr14 = _atr_wilder(maxima, minima, fechamento, 14)
    x["atr_rel"] = atr14 / fechamento
    x["regime_vol"] = x["atr_rel"] / x["atr_rel"].rolling(100, min_periods=50).mean()
    bb = bandas_bollinger(fechamento, 20, 2.0)
    x["bb_largura"] = (bb["superior"] - bb["inferior"]) / fechamento
    ema20 = _ema(fechamento, 20)
    atr10 = _atr_wilder(maxima, minima, fechamento, 10)
    dentro = (bb["superior"] < ema20 + 2 * atr10) & (bb["inferior"] > ema20 - 2 * atr10)
    x["squeeze"] = dentro.astype(float).where(bb["superior"].notna() & atr10.notna())

    # ---- volume
    x["volume_rel_5"] = volume / volume.shift(1).rolling(5, min_periods=5).mean()

    # ---- localizacao
    minima_50, maxima_50 = minima.rolling(50, min_periods=50).min(), maxima.rolling(50, min_periods=50).max()
    x["posicao_range_50"] = (fechamento - minima_50) / (maxima_50 - minima_50)
    x["dist_max_50_pct"] = maxima_50 / fechamento - 1
    x["dist_min_50_pct"] = 1 - minima_50 / fechamento

    # ---- vela
    amplitude = (maxima - minima).replace(0, np.nan)
    corpo = fechamento - abertura
    x["corpo_rel"] = corpo.abs() / amplitude
    sinal_vela = np.sign(corpo)
    x["vela"] = sinal_vela
    pavio_superior = maxima - np.maximum(abertura, fechamento)
    pavio_inferior = np.minimum(abertura, fechamento) - minima
    x["pavio_contra_rel"] = pd.Series(
        np.where(corpo >= 0, pavio_inferior, pavio_superior), index=quadro.index
    ) / amplitude
    grupo = (sinal_vela != sinal_vela.shift(1)).cumsum()
    x["sequencia"] = (sinal_vela.groupby(grupo).cumcount() + 1).clip(upper=8) * sinal_vela

    return x.replace([np.inf, -np.inf], np.nan)


def confluencia(sinais_por_setup: dict[str, pd.DataFrame], janela: int = 6) -> pd.DataFrame:
    """Quantos setups deram compra e quantos deram venda nas ultimas `janela` barras.

    Conta setups, nao sinais: um setup que disparou duas vezes na janela vale um.
    """
    if not sinais_por_setup:
        raise ValueError("confluencia precisa de pelo menos um setup")
    indice = next(iter(sinais_por_setup.values())).index
    comprando = pd.Series(0.0, index=indice)
    vendendo = pd.Series(0.0, index=indice)
    for sinais in sinais_por_setup.values():
        direcao = sinais["direcao"].reindex(indice).fillna(0)
        comprando += (direcao == 1).astype(float).rolling(janela, min_periods=1).max()
        vendendo += (direcao == -1).astype(float).rolling(janela, min_periods=1).max()
    return pd.DataFrame({"setups_comprando": comprando, "setups_vendendo": vendendo})


def no_instante(leituras: pd.DataFrame, posicao: int, direcao: int) -> dict:
    """As leituras de UMA barra, ja relativas a direcao do sinal.

    Direcionais viram `<nome>_a_favor` (positivo a favor do sinal). A confluencia
    desconta o proprio setup, que sempre esta na janela: e "quantos OUTROS".
    """
    linha = leituras.iloc[posicao]
    x: dict = {}
    for nome, valor in linha.items():
        if nome in ("setups_comprando", "setups_vendendo"):
            continue
        if nome in DIRECIONAIS:
            x[f"{nome}_a_favor"] = float(valor) * direcao if pd.notna(valor) else np.nan
        else:
            x[nome] = valor
    if "setups_comprando" in linha.index:
        mesma = linha["setups_comprando"] if direcao > 0 else linha["setups_vendendo"]
        contra = linha["setups_vendendo"] if direcao > 0 else linha["setups_comprando"]
        outros_a_favor = max(float(mesma) - 1.0, 0.0)
        x["confluencia_a_favor"] = outros_a_favor - float(contra)
        x["setups_contra"] = float(contra)
    return x


def descrever(x: dict) -> list[str]:
    """As leituras direcionais de um sinal em portugues, para a tela e o log."""
    rotulos = {
        "estrutura_a_favor": "estrutura", "tendencia_medias_a_favor": "medias",
        "tendencia_maior_a_favor": "grafico maior", "confluencia_a_favor": "confluencia",
        "inclinacao_ema50_a_favor": "inclinacao", "ret_maior_5_a_favor": "momento maior",
        "momento_20_a_favor": "momento", "macd_hist_rel_a_favor": "macd", "vela_a_favor": "vela",
    }
    frases = []
    for coluna, rotulo in rotulos.items():
        valor = x.get(coluna)
        if valor is None or (isinstance(valor, float) and np.isnan(valor)):
            continue
        if valor > 0:
            frases.append(f"{rotulo} a favor")
        elif valor < 0:
            frases.append(f"{rotulo} contra")
        else:
            frases.append(f"{rotulo} neutra")
    return frases


def catalogo_em_texto() -> str:
    linhas = ["LEITURAS DE GRAFICO (nome | familia | direcional | monotona | fonte)", ""]
    for l in (*CATALOGO, CONFLUENCIA):
        linhas.append(
            f"{l.nome:<20} {l.familia:<12} {'dir' if l.direcional else '   '} "
            f"{'mono' if l.monotona else '    '}  {l.fonte}"
        )
        linhas.append(f"{'':<20} {l.regra}")
    return "\n".join(linhas)
