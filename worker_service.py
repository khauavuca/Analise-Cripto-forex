import os
import signal
import time
from Market_Analyzer import AnalisadorMercado
from RiskManager import GerenciadorRisco
from Logger import iniciar_logger, salvar_log_json

def principal() -> int:
    logger = iniciar_logger("TrabalhadorBackEnd")
    intervalo = int(os.getenv("BACKGROUND_INTERVAL", "30"))
    logger.info(f"Trabalhador em BackEnd iniciado. Verificando mercado a cada {intervalo} segundos.")

    analisador = AnalisadorMercado()
    gerenciador_risco = GerenciadorRisco()

    parar_loop = False

    def manipulador_sinal(signum, frame):
        nonlocal parar_loop
        logger.info(f"Sinal de parada recebido ({signum}). Encerrando Trabalhador de Fundo graciosamente...")
        parar_loop = True

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, manipulador_sinal)
        except Exception:
            pass

    while not parar_loop:
        try:
            # 1. Dupla Validação: Extrair e avaliar mercado (Cripto/Forex)
            resultado_analise = analisador.varredura_dupla()
            
            if resultado_analise:
                # 2. Risk Management: Calcular distâncias, Pips e Bancas (sem executar ordem)
                relatorio_final = gerenciador_risco.calcular_dimensionamento(resultado_analise)
                
                # 3. Log Estruturado
                salvar_log_json(relatorio_final)
                
                logger.info(f"Varredura Concluída. Sinal Final: {relatorio_final['entrada']['sinal']}")
                logger.debug("--- DADOS GERADOS ---")
                import json
                print(json.dumps(relatorio_final, indent=4, ensure_ascii=False))
                print("-" * 50)
            else:
                logger.warning("Nenhum dado novo foi processado neste ciclo.")
            
        except Exception as erro:
            logger.error(f"Erro severo no loop do trabalhador: {erro}")
            
        for _ in range(intervalo):
            if parar_loop:
                break
            time.sleep(1)

    logger.info("Trabalhador finalizado com segurança.")
    return 0

if __name__ == "__main__":
    raise SystemExit(principal())
