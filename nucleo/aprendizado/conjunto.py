"""Monta o conjunto de treino: o que o sistema via no sinal, e o que aconteceu.

Cada linha e um trade. As colunas de entrada sao o painel de indicadores da
estrategia **na barra em que o sinal nasceu** - nem uma barra depois -, mais o
contexto do mercado e do proprio trade. O rotulo e o desfecho reconstruido
pelo motor.

Tres decisoes que evitam que o modelo aprenda besteira:

**Nivel de preco vira distancia relativa.** Uma media movel em 79.000 nao diz
nada sozinha, e nao se compara entre BTC e XRP. O que informa e "a media esta
1,2% abaixo do fechamento". Toda coluna que e um nivel de preco e convertida
para essa forma; toda coluna que e uma diferenca de preco (ATR, MACD) e
dividida pelo fechamento.

**A barra do sinal, nao a da entrada.** O motor executa na abertura da barra
seguinte. Usar o painel da barra de entrada daria ao modelo uma vela que o
operador ainda nao tinha visto quando decidiu.

**O rotulo so existe na saida.** Um trade aberto em `t` e fechado em `t+k` so
vira exemplo de treino depois de `t+k`. Quem faz a divisao temporal precisa
respeitar isso - `dividir_por_tempo` aplica a purga.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..estrategias.base import Estrategia

# Colunas do painel que sao NIVEL de preco: viram (nivel / fechamento - 1).
NIVEIS_DE_PRECO = (
    "sma_", "ema_", "bb_inferior", "bb_media", "bb_superior", "kc_inferior",
    "kc_superior", "vwap", "vwap_inferior", "vwap_superior", "topo", "fundo",
    "topo_anterior", "fundo_anterior", "suporte", "resistencia", "canal_alto",
    "canal_baixo", "filtro",
)
# Colunas que sao DIFERENCA de preco: viram (valor / fechamento).
DIFERENCAS_DE_PRECO = ("atr", "macd", "macd_sinal", "macd_histograma", "momento", "vwap_desvio")
# O resto ja e adimensional (rsi, adx, retorno, volatilidade, tendencia...).

COLUNAS_META = ["entrada", "saida", "par", "timeframe", "estrategia", "motivo_saida"]
COLUNAS_ROTULO = ["venceu", "multiplo_r", "retorno_liquido_pct"]


def _sem_prefixo_de_componente(coluna: str) -> str:
    """A estrategia composta prefixa as colunas com c0_, c1_... - tira isso."""
    if len(coluna) > 3 and coluna[0] == "c" and coluna[1].isdigit() and coluna[2] == "_":
        return coluna[3:]
    return coluna


def _e_diferenca(nome: str) -> bool:
    return nome in DIFERENCAS_DE_PRECO


def _e_nivel(nome: str) -> bool:
    return any(nome == n or nome.startswith(n) for n in NIVEIS_DE_PRECO)


def normalizar_painel(painel: pd.DataFrame, fechamento: pd.Series) -> pd.DataFrame:
    """Deixa o painel comparavel entre pares e ao longo do tempo."""
    saida = pd.DataFrame(index=painel.index)
    for coluna in painel.columns:
        serie = painel[coluna]
        nome = _sem_prefixo_de_componente(coluna)
        # Diferenca antes de nivel: "vwap_desvio" e uma diferenca, mas o
        # prefixo "vwap" o classificaria como nivel se a ordem fosse outra.
        if _e_diferenca(nome):
            saida[f"{coluna}_rel"] = serie / fechamento
        elif _e_nivel(nome):
            saida[f"{coluna}_rel"] = serie / fechamento - 1
        else:
            saida[coluna] = serie
    return saida.replace([np.inf, -np.inf], np.nan)


def contexto_de_mercado(quadro: pd.DataFrame) -> pd.DataFrame:
    """Sinais genericos que toda estrategia se beneficia de enxergar."""
    fechamento = quadro["fechamento"]
    amplitude = (quadro["maxima"] - quadro["minima"]) / fechamento
    log_ret = np.log(fechamento / fechamento.shift(1))
    ctx = pd.DataFrame(
        {
            "ret_1": fechamento.pct_change(1),
            "ret_5": fechamento.pct_change(5),
            "ret_20": fechamento.pct_change(20),
            "vol_20": log_ret.rolling(20, min_periods=20).std(ddof=1),
            "amplitude_media_10": amplitude.rolling(10, min_periods=10).mean(),
            "posicao_no_range_20": (
                (fechamento - quadro["minima"].rolling(20).min())
                / (quadro["maxima"].rolling(20).max() - quadro["minima"].rolling(20).min())
            ),
            "volume_rel_20": quadro["volume"] / quadro["volume"].rolling(20, min_periods=20).mean(),
            "hora": quadro.index.hour,
            "dia_semana": quadro.index.dayofweek,
        },
        index=quadro.index,
    )
    return ctx.replace([np.inf, -np.inf], np.nan)


@dataclass
class Conjunto:
    """Entradas, rotulos e metadados alinhados linha a linha."""

    entradas: pd.DataFrame
    rotulos: pd.DataFrame
    meta: pd.DataFrame

    def __len__(self) -> int:
        return len(self.entradas)

    @property
    def vazio(self) -> bool:
        return len(self) == 0

    def concatenar(self, outro: "Conjunto") -> "Conjunto":
        return Conjunto(
            pd.concat([self.entradas, outro.entradas], ignore_index=True),
            pd.concat([self.rotulos, outro.rotulos], ignore_index=True),
            pd.concat([self.meta, outro.meta], ignore_index=True),
        )

    def quadro_completo(self) -> pd.DataFrame:
        return pd.concat([self.meta, self.rotulos, self.entradas], axis=1)

    def ordenar_por_tempo(self) -> "Conjunto":
        ordem = self.meta["entrada"].argsort().to_numpy()
        return Conjunto(
            self.entradas.iloc[ordem].reset_index(drop=True),
            self.rotulos.iloc[ordem].reset_index(drop=True),
            self.meta.iloc[ordem].reset_index(drop=True),
        )


def vazio() -> Conjunto:
    return Conjunto(pd.DataFrame(), pd.DataFrame(columns=COLUNAS_ROTULO), pd.DataFrame(columns=COLUNAS_META))


def caracteristicas_no_instante(
    painel_normalizado: pd.DataFrame,
    contexto: pd.DataFrame,
    fechamento: float,
    posicao: int,
    direcao: int,
    forca: float,
    stop: float,
    alvo: float,
) -> dict:
    """A linha de entrada do modelo para UM sinal, na barra em que ele nasceu.

    E a mesma funcao usada para montar o treino e para pontuar um sinal ao
    vivo. Duas implementacoes divergiriam com o tempo, e o modelo passaria a
    ver em producao colunas diferentes das que aprendeu.
    """
    x = {}
    x.update(painel_normalizado.iloc[posicao].to_dict())
    x.update(contexto.iloc[posicao].to_dict())
    x["direcao"] = int(direcao)
    x["forca"] = 0.0 if pd.isna(forca) else float(forca)
    x["dist_stop_pct"] = abs(fechamento - float(stop)) / fechamento
    x["dist_alvo_pct"] = abs(float(alvo) - fechamento) / fechamento
    x["razao_alvo_stop"] = (
        x["dist_alvo_pct"] / x["dist_stop_pct"] if x["dist_stop_pct"] > 0 else np.nan
    )
    return x


def montar(
    quadro: pd.DataFrame,
    estrategia: Estrategia,
    trades: pd.DataFrame,
    par: str = "",
    timeframe: str = "",
    atraso_barras: int = 1,
) -> Conjunto:
    """Um exemplo por trade fechado, com o que o sistema via no sinal."""
    if trades is None or trades.empty:
        return vazio()

    fechados = trades[trades["motivo_saida"] != "FIM_DADOS"]
    if fechados.empty:
        return vazio()

    painel = normalizar_painel(estrategia.painel_indicadores(quadro), quadro["fechamento"])
    contexto = contexto_de_mercado(quadro)
    posicoes = {momento: i for i, momento in enumerate(quadro.index)}

    linhas_x, linhas_y, linhas_m = [], [], []
    for trade in fechados.itertuples():
        pos_entrada = posicoes.get(trade.entrada)
        if pos_entrada is None:
            continue
        pos_sinal = pos_entrada - atraso_barras
        if pos_sinal < 0:
            continue

        linhas_x.append(
            caracteristicas_no_instante(
                painel, contexto, float(quadro["fechamento"].iloc[pos_sinal]), pos_sinal,
                trade.direcao, trade.forca, trade.stop, trade.alvo,
            )
        )

        linhas_y.append(
            {
                "venceu": bool(trade.retorno_liquido_pct > 0),
                "multiplo_r": float(trade.multiplo_r) if not pd.isna(trade.multiplo_r) else np.nan,
                "retorno_liquido_pct": float(trade.retorno_liquido_pct),
            }
        )
        linhas_m.append(
            {
                "entrada": trade.entrada,
                "saida": trade.saida,
                "par": par,
                "timeframe": timeframe,
                "estrategia": estrategia.nome,
                "motivo_saida": trade.motivo_saida,
            }
        )

    if not linhas_x:
        return vazio()
    return Conjunto(pd.DataFrame(linhas_x), pd.DataFrame(linhas_y), pd.DataFrame(linhas_m))


def dividir_por_tempo(
    conjunto: Conjunto, corte: pd.Timestamp
) -> tuple[Conjunto, Conjunto]:
    """Treino antes do corte, teste depois - com purga.

    A purga e o detalhe que separa validacao honesta de vazamento: um trade
    que ENTROU antes do corte mas SAIU depois tem rotulo que so ficou conhecido
    dentro do periodo de teste. Ele fica de fora do treino. Sem isso, o modelo
    aprende com desfechos que, naquele momento, ainda nao existiam.
    """
    meta = conjunto.meta
    entrada = pd.to_datetime(meta["entrada"], utc=True)
    saida = pd.to_datetime(meta["saida"], utc=True)

    treino = (saida < corte).to_numpy()
    teste = (entrada >= corte).to_numpy()

    def fatiar(mascara: np.ndarray) -> Conjunto:
        return Conjunto(
            conjunto.entradas[mascara].reset_index(drop=True),
            conjunto.rotulos[mascara].reset_index(drop=True),
            conjunto.meta[mascara].reset_index(drop=True),
        )

    return fatiar(treino), fatiar(teste)


def salvar_csv(conjunto: Conjunto, caminho: str) -> None:
    """Grava meta, rotulos e entradas lado a lado, uma linha por trade."""
    from pathlib import Path

    Path(caminho).parent.mkdir(parents=True, exist_ok=True)
    conjunto.quadro_completo().to_csv(caminho, index=False)


def ler_csv(caminho: str) -> Conjunto:
    """Reconstroi o conjunto a partir do CSV de `salvar_csv`."""
    quadro = pd.read_csv(caminho)
    if quadro.empty:
        return vazio()
    for coluna in ("entrada", "saida"):
        quadro[coluna] = pd.to_datetime(quadro[coluna], utc=True, format="mixed")
    quadro["venceu"] = quadro["venceu"].astype(bool)
    entradas = quadro.drop(columns=COLUNAS_META + COLUNAS_ROTULO)
    return Conjunto(entradas, quadro[COLUNAS_ROTULO].copy(), quadro[COLUNAS_META].copy())


def cortes_por_meses(conjunto: Conjunto, meses_teste: int = 3, minimo_treino: int = 60) -> list[pd.Timestamp]:
    """Pontos de corte para walk-forward, a cada `meses_teste` meses."""
    if conjunto.vazio:
        return []
    entrada = pd.to_datetime(conjunto.meta["entrada"], utc=True).sort_values()
    inicio, fim = entrada.iloc[0], entrada.iloc[-1]
    cortes = []
    corte = inicio + pd.DateOffset(months=meses_teste)
    while corte < fim:
        if (entrada < corte).sum() >= minimo_treino:
            cortes.append(pd.Timestamp(corte))
        corte = corte + pd.DateOffset(months=meses_teste)
    return cortes
