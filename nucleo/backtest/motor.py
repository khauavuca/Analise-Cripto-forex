"""Simulacao vela a vela.

Este arquivo e o que decide se um numero de assertividade vale alguma coisa.
As quatro escolhas que mais importam estao documentadas onde acontecem:

1. Decide no fechamento da barra `i`, executa na **abertura** da barra `i+1`.
2. Quando a barra toca stop e alvo, assume **stop** - erro para o lado seguro.
3. Se a barra **abre** alem do stop, o preenchimento e na abertura, nao no stop.
4. Taxa e slippage entram **por trade**, nao como desconto no fim da curva.

O laco e um `for` em Python de proposito. Da para vetorizar entrada e saida
simples, mas nao a logica de stop, que depende do caminho percorrido. E o unico
ponto do sistema onde um bug produz um numero **plausivel e errado** em vez de
um erro - e ai clareza vale mais que esperteza.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..estrategias.base import COMPRA, NEUTRO, VENDA, validar_sinais

MOTIVO_STOP = "STOP"
MOTIVO_ALVO = "ALVO"
MOTIVO_TEMPO = "TEMPO"
MOTIVO_FIM = "FIM_DADOS"

COLUNAS_TRADE = [
    "direcao", "entrada", "preco_entrada", "saida", "preco_saida", "motivo_saida",
    "barras_no_trade", "retorno_bruto_pct", "retorno_liquido_pct", "multiplo_r",
    "mfe_pct", "mae_pct", "ambiguo", "stop", "alvo", "forca", "fracao",
    "motivo_entrada",
]


def fracao_do_capital(
    preco_entrada: float, stop: float, config: ConfigExecucao
) -> float:
    """Quanto do capital entra neste trade.

    No modo "risco", a posicao e inversamente proporcional a distancia do stop,
    com teto de exposicao - sem o teto, um stop a 0,1% pediria vinte vezes a
    conta para "arriscar so 2%".
    """
    if config.dimensionamento == "fixo":
        return config.fracao_por_trade

    distancia = abs(preco_entrada - stop) / preco_entrada
    if distancia <= 0:
        return 0.0
    return min(config.risco_por_trade / distancia, config.exposicao_maxima)


@dataclass(frozen=True)
class ModeloCustos:
    """Custo de negociar. Os padroes sao spot taker da Binance.

    Ida e volta ficam em torno de 0,2% de taxa mais 0,1% de slippage - ou seja,
    **uma estrategia precisa de mais de 0,3% brutos por trade so para empatar**.
    E esse numero que justifica operar em 1h/4h em vez de 5m.
    """

    taxa_por_lado: float = 0.001
    slippage_por_lado: float = 0.0005

    @property
    def custo_ida_e_volta(self) -> float:
        return 2 * (self.taxa_por_lado + self.slippage_por_lado)


@dataclass(frozen=True)
class ConfigExecucao:
    """Como as ordens sao simuladas.

    `dimensionamento` decide quanto capital entra em cada trade e muda o
    resultado mais do que parece:

    - "fixo": sempre a mesma fracao do capital. Simples, mas ignora que um
      trade com stop a 1% e um com stop a 5% carregam riscos bem diferentes.
    - "risco": a posicao e calculada para que bater o stop custe sempre
      `risco_por_trade` do capital - a formula do gestor de risco. E o que faz
      a curva de capital concordar com a expectancia em R.
    """

    max_barras_no_trade: int = 48
    atraso_barras: int = 1
    ambiguidade: str = "pessimista"
    dimensionamento: str = "risco"
    fracao_por_trade: float = 1.0
    risco_por_trade: float = 0.02
    exposicao_maxima: float = 1.0
    # Move o stop para o preco de entrada assim que o trade acumular este
    # tanto de lucro, medido em multiplos do risco. None desliga.
    gatilho_empate: float | None = None


@dataclass
class ResultadoBacktest:
    trades: pd.DataFrame
    curva_capital: pd.Series
    diagnosticos: dict = field(default_factory=dict)


@dataclass
class _Posicao:
    direcao: int
    indice_entrada: int
    momento_entrada: pd.Timestamp
    preco_entrada: float
    stop: float
    alvo: float
    forca: float
    fracao: float
    motivo: str
    stop_original: float = 0.0
    empatado: bool = False
    maior_favor: float = 0.0
    maior_contra: float = 0.0


def executar(
    quadro: pd.DataFrame,
    sinais: pd.DataFrame,
    custos: ModeloCustos | None = None,
    config: ConfigExecucao | None = None,
) -> ResultadoBacktest:
    custos = custos or ModeloCustos()
    config = config or ConfigExecucao()
    validar_sinais(sinais, quadro.index)

    if config.ambiguidade not in ("pessimista", "otimista"):
        raise ValueError("ambiguidade deve ser 'pessimista' ou 'otimista'.")

    # O UNICO deslocamento do sistema. A linha `i` das ordens carrega o sinal
    # decidido no fechamento da barra `i - atraso`, e sera executado na
    # abertura da barra `i`. Executar no fechamento da propria barra que gerou
    # o sinal e a batota classica: em estrategia de reversao a media isso
    # sozinho transforma um sistema perdedor em vencedor, porque voce compra
    # exatamente no preco que fez o indicador disparar.
    ordens = sinais.shift(config.atraso_barras)

    aberturas = quadro["abertura"].to_numpy(dtype=float)
    maximas = quadro["maxima"].to_numpy(dtype=float)
    minimas = quadro["minima"].to_numpy(dtype=float)
    fechamentos = quadro["fechamento"].to_numpy(dtype=float)

    direcoes = ordens["direcao"].fillna(NEUTRO).to_numpy(dtype=float)
    stops = ordens["stop"].to_numpy(dtype=float)
    alvos = ordens["alvo"].to_numpy(dtype=float)
    forcas = ordens["forca"].fillna(0.0).to_numpy(dtype=float)
    motivos = ordens["motivo"].fillna("").to_numpy()

    momentos = quadro.index
    total = len(quadro)

    capital = 1.0
    curva = np.empty(total, dtype=float)
    trades: list[dict] = []
    ambiguidades = 0
    posicao: _Posicao | None = None

    for i in range(total):
        tinha_posicao = posicao is not None

        if posicao is not None:
            # A saida e avaliada com o stop que valia no FIM da barra anterior.
            # Mover o stop usando a maxima desta barra e logo em seguida testar
            # esse stop contra a minima da mesma barra seria decidir com
            # informacao de dentro da barra - a ordem dos dois eventos nao esta
            # no OHLC.
            saida = _avaliar_saida(posicao, i, aberturas[i], maximas[i], minimas[i], config)
            posicao = _atualizar_extremos(posicao, maximas[i], minimas[i])

            if saida is not None:
                preco_saida, motivo_saida, foi_ambiguo = saida
                ambiguidades += int(foi_ambiguo)
                trade = _fechar(
                    posicao, i, momentos[i], preco_saida, motivo_saida, foi_ambiguo, custos
                )
                trades.append(trade)
                capital *= 1 + trade["retorno_liquido_pct"] * trade["fracao"]
                posicao = None
            else:
                posicao = _talvez_empatar(posicao, config)

        # Nao abre posicao numa barra em que ja havia posicao aberta: a entrada
        # aconteceria na abertura, ou seja, antes da saida que acabou de ocorrer.
        if posicao is None and not tinha_posicao and direcoes[i] != NEUTRO:
            fracao = (
                fracao_do_capital(aberturas[i], float(stops[i]), config)
                if not np.isnan(stops[i])
                else 0.0
            )
            if not (np.isnan(stops[i]) or np.isnan(alvos[i])) and fracao > 0:
                posicao = _Posicao(
                    direcao=int(direcoes[i]),
                    indice_entrada=i,
                    momento_entrada=momentos[i],
                    preco_entrada=aberturas[i],
                    stop=float(stops[i]),
                    alvo=float(alvos[i]),
                    forca=float(forcas[i]),
                    fracao=fracao,
                    motivo=str(motivos[i]),
                    stop_original=float(stops[i]),
                )
                # A entrada foi na abertura desta barra, entao o que a barra
                # percorreu depois disso ja conta para MFE e MAE - e tambem
                # pode ter batido no stop ou no alvo.
                posicao = _atualizar_extremos(posicao, maximas[i], minimas[i])
                saida = _avaliar_saida(posicao, i, aberturas[i], maximas[i], minimas[i], config)
                if saida is not None:
                    preco_saida, motivo_saida, foi_ambiguo = saida
                    ambiguidades += int(foi_ambiguo)
                    trade = _fechar(
                        posicao, i, momentos[i], preco_saida, motivo_saida, foi_ambiguo, custos
                    )
                    trades.append(trade)
                    capital *= 1 + trade["retorno_liquido_pct"] * trade["fracao"]
                    posicao = None
                else:
                    posicao = _talvez_empatar(posicao, config)

        curva[i] = _marcar_a_mercado(capital, posicao, fechamentos[i], custos)

    if posicao is not None:
        # Posicao aberta no fim do arquivo nao e resultado - e o backtest
        # acabando. Fica marcada para sair das estatisticas de acerto.
        trade = _fechar(
            posicao, total - 1, momentos[-1], fechamentos[-1], MOTIVO_FIM, False, custos
        )
        trades.append(trade)
        capital *= 1 + trade["retorno_liquido_pct"] * trade["fracao"]
        curva[-1] = capital

    quadro_trades = pd.DataFrame(trades, columns=COLUNAS_TRADE)
    fechados = quadro_trades[quadro_trades["motivo_saida"] != MOTIVO_FIM]

    diagnosticos = {
        "n_trades": int(len(quadro_trades)),
        "n_trades_fechados": int(len(fechados)),
        "saidas_ambiguas": int(ambiguidades),
        "pct_saidas_ambiguas": (
            float(ambiguidades / len(fechados)) if len(fechados) else 0.0
        ),
        "custo_ida_e_volta": custos.custo_ida_e_volta,
        "custo_total_pct": float(
            quadro_trades["retorno_bruto_pct"].sum()
            - quadro_trades["retorno_liquido_pct"].sum()
        ),
        "atraso_barras": config.atraso_barras,
        "ambiguidade": config.ambiguidade,
        "dimensionamento": config.dimensionamento,
        "risco_por_trade": config.risco_por_trade,
        "fracao_media": (
            float(quadro_trades["fracao"].mean()) if len(quadro_trades) else 0.0
        ),
        "barras": total,
    }

    return ResultadoBacktest(
        trades=quadro_trades,
        curva_capital=pd.Series(curva, index=momentos, name="capital"),
        diagnosticos=diagnosticos,
    )


def _talvez_empatar(posicao: _Posicao, config: ConfigExecucao) -> _Posicao:
    """Sobe o stop para o preco de entrada depois de um lucro minimo.

    Existe por causa de um numero medido: 45% dos trades perdedores chegaram a
    mais de 0,5R de lucro antes de virar e ainda pagarem 1R. Se essa reversao
    for sistematica, empatar converte parte dessas perdas em zero; se nao for,
    o custo aparece como vencedores mortos no meio do caminho. Quem decide e o
    backtest, nao a intuicao.
    """
    if config.gatilho_empate is None or posicao.empatado:
        return posicao

    referencia = posicao.stop_original or posicao.stop
    risco = abs(posicao.preco_entrada - referencia) / posicao.preco_entrada
    if risco <= 0:
        return posicao

    if posicao.maior_favor / risco >= config.gatilho_empate:
        posicao.stop = posicao.preco_entrada
        posicao.empatado = True
    return posicao


def _atualizar_extremos(posicao: _Posicao, maxima: float, minima: float) -> _Posicao:
    """Acompanha o melhor e o pior momento do trade (MFE e MAE).

    Sao eles que permitem depois calibrar stop e alvo com dado realizado em vez
    de palpite: se o MAE tipico dos vencedores e 0,8%, um stop de 0,4% esta
    matando trades que teriam dado certo.
    """
    if posicao.direcao == COMPRA:
        favor = (maxima - posicao.preco_entrada) / posicao.preco_entrada
        contra = (minima - posicao.preco_entrada) / posicao.preco_entrada
        posicao.maior_favor = max(posicao.maior_favor, favor)
        posicao.maior_contra = min(posicao.maior_contra, contra)
    else:
        favor = (posicao.preco_entrada - minima) / posicao.preco_entrada
        contra = (posicao.preco_entrada - maxima) / posicao.preco_entrada
        posicao.maior_favor = max(posicao.maior_favor, favor)
        posicao.maior_contra = min(posicao.maior_contra, contra)
    return posicao


def _avaliar_saida(
    posicao: _Posicao,
    indice: int,
    abertura: float,
    maxima: float,
    minima: float,
    config: ConfigExecucao,
) -> tuple[float, str, bool] | None:
    """Devolve (preco, motivo, ambiguo) ou None se a posicao continua aberta."""
    comprado = posicao.direcao == COMPRA
    barras = indice - posicao.indice_entrada
    if barras < 0:
        return None

    if barras > 0:
        # 1. Gap na abertura. Se a barra ja abre alem do stop, o preenchimento
        #    sai na abertura - pior que o stop. Assumir o preco do stop aqui
        #    esconde sistematicamente as perdas de cauda, que sao as que
        #    quebram a conta.
        if comprado and abertura <= posicao.stop:
            return abertura, MOTIVO_STOP, False
        if not comprado and abertura >= posicao.stop:
            return abertura, MOTIVO_STOP, False
        if comprado and abertura >= posicao.alvo:
            return abertura, MOTIVO_ALVO, False
        if not comprado and abertura <= posicao.alvo:
            return abertura, MOTIVO_ALVO, False

        # 2. Prazo esgotado: sai na abertura desta barra, decisao ja conhecida.
        if barras >= config.max_barras_no_trade:
            return abertura, MOTIVO_TEMPO, False
    # Na barra de entrada (barras == 0) nao ha gap - a abertura E a entrada -
    # e o prazo ainda nao correu. Mas o resto da barra acontece DEPOIS da
    # entrada, e o stop pode ser atingido nela. Pular essa checagem garantia
    # que todo trade sobrevivesse a primeira barra, o que e otimismo puro.

    tocou_stop = minima <= posicao.stop if comprado else maxima >= posicao.stop
    tocou_alvo = maxima >= posicao.alvo if comprado else minima <= posicao.alvo

    # 3. Os dois na mesma barra. O OHLC nao diz qual veio primeiro, e nenhuma
    #    convencao e "correta". Assumir o stop e pessimista de proposito: se a
    #    estrategia ganha dinheiro sob a hipotese ruim, provavelmente ganha
    #    mesmo. Se so ganha sob a hipotese boa, voce nao aprendeu nada.
    if tocou_stop and tocou_alvo:
        if config.ambiguidade == "pessimista":
            return posicao.stop, MOTIVO_STOP, True
        return posicao.alvo, MOTIVO_ALVO, True

    if tocou_stop:
        return posicao.stop, MOTIVO_STOP, False
    if tocou_alvo:
        return posicao.alvo, MOTIVO_ALVO, False
    return None


def _precos_efetivos(
    direcao: int, preco_entrada: float, preco_saida: float, custos: ModeloCustos
) -> tuple[float, float]:
    """Aplica slippage sempre contra o operador."""
    entrada = preco_entrada * (1 + direcao * custos.slippage_por_lado)
    saida = preco_saida * (1 - direcao * custos.slippage_por_lado)
    return entrada, saida


def _fechar(
    posicao: _Posicao,
    indice: int,
    momento: pd.Timestamp,
    preco_saida: float,
    motivo_saida: str,
    ambiguo: bool,
    custos: ModeloCustos,
) -> dict:
    bruto = (
        posicao.direcao
        * (preco_saida - posicao.preco_entrada)
        / posicao.preco_entrada
    )

    entrada_efetiva, saida_efetiva = _precos_efetivos(
        posicao.direcao, posicao.preco_entrada, preco_saida, custos
    )
    liquido = (
        posicao.direcao * (saida_efetiva - entrada_efetiva) / entrada_efetiva
        - 2 * custos.taxa_por_lado
    )

    # Sempre o stop original: se o stop moveu para o empate, medir R contra o
    # stop novo faria a unidade encolher e inflaria o resultado.
    risco = abs(entrada_efetiva - (posicao.stop_original or posicao.stop))
    multiplo_r = (
        posicao.direcao * (saida_efetiva - entrada_efetiva) / risco
        if risco > 0
        else np.nan
    )

    return {
        "direcao": posicao.direcao,
        "entrada": posicao.momento_entrada,
        "preco_entrada": posicao.preco_entrada,
        "saida": momento,
        "preco_saida": preco_saida,
        "motivo_saida": motivo_saida,
        "barras_no_trade": indice - posicao.indice_entrada,
        "retorno_bruto_pct": bruto,
        "retorno_liquido_pct": liquido,
        "multiplo_r": multiplo_r,
        "mfe_pct": posicao.maior_favor,
        "mae_pct": posicao.maior_contra,
        "ambiguo": ambiguo,
        # O stop com que o trade nasceu, nao o movido. E dele que sai a unidade
        # de risco na calibragem; guardar o stop no empate faria o risco virar
        # zero e o trade sumir da analise.
        "stop": posicao.stop_original or posicao.stop,
        "alvo": posicao.alvo,
        "forca": posicao.forca,
        "fracao": posicao.fracao,
        "motivo_entrada": posicao.motivo,
    }


def _marcar_a_mercado(
    capital: float,
    posicao: _Posicao | None,
    fechamento: float,
    custos: ModeloCustos,
) -> float:
    """Capital incluindo o resultado nao realizado da posicao aberta.

    Drawdown calculado so com trades fechados esconde a dor de dentro do trade
    - justamente a que faria alguem desligar o sistema na vida real.
    """
    if posicao is None:
        return capital

    entrada_efetiva, saida_efetiva = _precos_efetivos(
        posicao.direcao, posicao.preco_entrada, fechamento, custos
    )
    nao_realizado = (
        posicao.direcao * (saida_efetiva - entrada_efetiva) / entrada_efetiva
        - 2 * custos.taxa_por_lado
    )
    return capital * (1 + nao_realizado * posicao.fracao)
