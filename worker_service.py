"""Servico que acompanha o mercado e registra sinais.

Nao envia ordem nenhuma. Ele le velas fechadas, roda a MESMA estrategia usada
no backtest e grava o que encontrou.

Duas mudancas em relacao a versao anterior:

- **Acorda no fechamento da vela, nao a cada 30 segundos.** A estrategia decide
  no fechamento; consultar de 30 em 30 segundos gerava mais de cem leituras
  redundantes por decisao, e o "sinal" mudava de valor no meio da vela porque
  o fechamento ainda estava se formando.
- **Mesmo codigo do backtest.** A estrategia recebe o quadro inteiro e devolve
  a coluna inteira; aqui so lemos a ultima linha. Sem isso o caminho ao vivo e
  o simulado divergem, e ai o backtest deixa de significar alguma coisa.
"""
from __future__ import annotations

import os
import signal
import time
from datetime import datetime, timedelta, timezone

from Logger import iniciar_logger, salvar_log_json
from nucleo.dados.armazenamento import Armazenamento
from nucleo.dados.carregador import carregar
from nucleo.dados.provedor import duracao_ms
from nucleo.dados.provedor_ccxt import ProvedorCCXT
from nucleo.estrategias.cruzamento_ema import EstrategiaCruzamentoEma
from nucleo.estrategias.rsi_macd import EstrategiaRsiMacd
from nucleo.risco import gerenciador as risco

FOLGA_SEGUNDOS = 20


def montar_estrategia(nome: str):
    if nome == "ema":
        return EstrategiaCruzamentoEma()
    if nome == "confluencia":
        from nucleo.estrategias.composta import EstrategiaComposta

        return EstrategiaComposta(
            [EstrategiaRsiMacd(), EstrategiaCruzamentoEma()], limiar=0.4
        )
    return EstrategiaRsiMacd()


def segundos_ate_proxima_vela(timeframe: str) -> float:
    """Quanto falta para a vela corrente fechar, com folga para a corretora."""
    passo = duracao_ms(timeframe) / 1000
    agora = datetime.now(timezone.utc).timestamp()
    return (passo - (agora % passo)) + FOLGA_SEGUNDOS


def uma_varredura(
    par: str, timeframe: str, estrategia, provedor, armazenamento, logger
) -> dict | None:
    barras = estrategia.barras_de_aquecimento() + 50
    quadro = carregar(
        par,
        timeframe,
        datetime.now(timezone.utc)
        - timedelta(milliseconds=duracao_ms(timeframe) * barras),
        provedor=provedor,
        armazenamento=armazenamento,
        barras_aquecimento=estrategia.barras_de_aquecimento(),
    )
    if quadro.empty:
        logger.warning(f"{par} {timeframe}: nenhuma vela disponivel.")
        return None

    sinais = estrategia.gerar_sinais(quadro)
    ultima = sinais.iloc[-1]
    direcao = int(ultima.direcao)

    relatorio = {
        "par": par,
        "timeframe": timeframe,
        "estrategia": estrategia.nome,
        "vela": str(sinais.index[-1]),
        "fechamento": float(quadro.fechamento.iloc[-1]),
        "sinal": {1: "COMPRA", -1: "VENDA", 0: "NEUTRO"}[direcao],
        "confianca": float(ultima.forca),
        "motivo": str(ultima.motivo),
        "marca_tempo": datetime.now(timezone.utc).isoformat(),
    }

    if direcao != 0:
        relatorio["protecoes"] = {"stop": float(ultima.stop), "alvo": float(ultima.alvo)}
        tamanho = risco.dimensionar(
            float(quadro.fechamento.iloc[-1]),
            float(ultima.stop),
            risco.config_do_ambiente(),
        )
        relatorio["gestao_risco"] = {
            "quantidade": tamanho.quantidade,
            "valor_exposto": tamanho.valor_exposto,
            "risco_em_moeda": tamanho.risco_em_moeda,
            "distancia_stop_pct": tamanho.distancia_stop_pct,
            "viavel": tamanho.viavel,
            "observacao": tamanho.observacao,
        }

    return relatorio


def principal() -> int:
    logger = iniciar_logger("Vigia")
    pares = [p.strip() for p in os.getenv("PARES", "BTC/USDT,ETH/USDT").split(",")]
    timeframe = os.getenv("TIMEFRAME", "4h")
    estrategia = montar_estrategia(os.getenv("ESTRATEGIA", "rsi_macd"))

    provedor = ProvedorCCXT()
    armazenamento = Armazenamento()
    logger.info(
        f"Vigiando {', '.join(pares)} em {timeframe} na {provedor.nome} com "
        f"{estrategia.nome}. Nenhuma ordem sera enviada."
    )

    parar = False

    def encerrar(numero, _quadro):
        nonlocal parar
        logger.info(f"Sinal {numero} recebido; encerrando com calma.")
        parar = True

    for evento in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(evento, encerrar)
        except (ValueError, OSError):
            pass

    while not parar:
        for par in pares:
            if parar:
                break
            try:
                relatorio = uma_varredura(
                    par, timeframe, estrategia, provedor, armazenamento, logger
                )
            except Exception as erro:
                logger.error(f"{par}: falha na varredura - {erro}")
                continue

            if relatorio:
                salvar_log_json(relatorio)
                registrar = (
                    logger.info if relatorio["sinal"] != "NEUTRO" else logger.debug
                )
                registrar(
                    f"{par} {timeframe} @ {relatorio['vela']}: {relatorio['sinal']} "
                    f"({relatorio['confianca']:.0%})"
                )

        espera = segundos_ate_proxima_vela(timeframe)
        logger.info(f"Proxima vela de {timeframe} em {espera / 60:.1f} min.")
        while espera > 0 and not parar:
            time.sleep(min(1.0, espera))
            espera -= 1

    armazenamento.fechar()
    logger.info("Vigia encerrado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(principal())
