import json
import logging
import os
from datetime import datetime

def iniciar_logger(nome_logger: str = "ScriptTrader") -> logging.Logger:
    """Configura logger que imprime no console."""
    logger = logging.getLogger(nome_logger)
    logger.setLevel(logging.INFO)
    fmt = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

    if not logger.handlers:
        console_handler = logging.StreamHandler()
        console_formatter = logging.Formatter(fmt)
        console_handler.setFormatter(console_formatter)
        logger.addHandler(console_handler)

    return logger

def salvar_log_json(dados_analise: dict, pasta_logs: str | None = None) -> None:
    """Salva os resultados diretamente em formato JSON estruturado."""
    if pasta_logs is None:
        pasta_logs = os.path.join(
            os.path.dirname(__file__),
            "logs",
            f"{datetime.now():%Y-%m}"
        )
    os.makedirs(pasta_logs, exist_ok=True)

    arquivo = os.path.join(
        pasta_logs,
        f"db_fallback_analise_{datetime.now():%Y%m%d}.json"
    )

    try:
        if not os.path.exists(arquivo):
            with open(arquivo, "w", encoding="utf-8") as f:
                json.dump([dados_analise], f, indent=4, ensure_ascii=False)
        else:
            with open(arquivo, "r", encoding="utf-8") as f:
                logs_existentes = json.load(f)
            
            logs_existentes.append(dados_analise)
            
            with open(arquivo, "w", encoding="utf-8") as f:
                json.dump(logs_existentes, f, indent=4, ensure_ascii=False)

    except Exception as erro:
        print(f"Erro ao salvar log JSON estruturado: {erro}")
