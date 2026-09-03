"""Registro em console, arquivo rotativo e JSONL."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from logging.handlers import TimedRotatingFileHandler

FORMATO = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
PASTA_PADRAO = os.path.join(os.path.dirname(__file__), "logs")


def iniciar_logger(nome_logger: str = "ScriptTrader") -> logging.Logger:
    """Logger com saida no console e em arquivo diario.

    A versao anterior so escrevia no console, embora o README prometesse
    arquivos com retencao - ou seja, um servico rodando em Docker nao deixava
    rastro nenhum depois que o contêiner reiniciava.
    """
    logger = logging.getLogger(nome_logger)
    logger.setLevel(os.getenv("NIVEL_LOG", "INFO").upper())

    if logger.handlers:
        return logger

    formatador = logging.Formatter(FORMATO)

    console = logging.StreamHandler()
    console.setFormatter(formatador)
    logger.addHandler(console)

    try:
        os.makedirs(PASTA_PADRAO, exist_ok=True)
        arquivo = TimedRotatingFileHandler(
            os.path.join(PASTA_PADRAO, "servico.log"),
            when="midnight",
            backupCount=int(os.getenv("RETENCAO_LOG_DIAS", "15")),
            encoding="utf-8",
        )
        arquivo.setFormatter(formatador)
        logger.addHandler(arquivo)
    except OSError as erro:
        # Sem permissao de escrita o servico continua: perder log e ruim,
        # parar de analisar por causa disso e pior.
        console.handle(
            logging.LogRecord(
                nome_logger, logging.WARNING, __file__, 0,
                f"Sem log em arquivo ({erro}); seguindo so com console.", (), None,
            )
        )

    return logger


def salvar_log_json(dados_analise: dict, pasta_logs: str | None = None) -> None:
    """Acrescenta um registro ao arquivo do dia, em JSONL.

    Uma linha por registro, aberto em modo append. A versao anterior relia e
    reescrevia o array JSON inteiro a cada chamada: custo quadratico no tamanho
    do arquivo e, pior, um encerramento no meio da escrita corrompia o arquivo
    e todos os registros seguintes daquele dia falhavam em silencio.
    """
    pasta = pasta_logs or os.path.join(PASTA_PADRAO, f"{datetime.now():%Y-%m}")

    try:
        os.makedirs(pasta, exist_ok=True)
        arquivo = os.path.join(pasta, f"sinais_{datetime.now():%Y%m%d}.jsonl")
        registro = {"registrado_em": datetime.now(timezone.utc).isoformat(), **dados_analise}
        with open(arquivo, "a", encoding="utf-8") as saida:
            saida.write(json.dumps(registro, ensure_ascii=False, default=str) + "\n")
    except OSError as erro:
        logging.getLogger("Logger").error(f"Nao consegui gravar o sinal em disco: {erro}")
