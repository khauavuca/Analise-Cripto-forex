import os
from Logger import iniciar_logger

class GerenciadorRisco:
    """
    Gerenciador focado apenas em avaliação financeira preventiva.
    Calcula valor do lote financeiro adequado para evitar explosão de banca.
    """
    def __init__(self, saldo_banca=1000.0, risco_maximo_por_trade=0.02):
        self.logger = iniciar_logger("GerenciadorRisco")
        self.saldo_banca = float(os.getenv("ACCOUNT_BALANCE", saldo_banca))
        self.risco_maximo = float(os.getenv("MAX_RISK", risco_maximo_por_trade))
        
        self.logger.info(f"Banca: ${self.saldo_banca} | Risco: {self.risco_maximo * 100}%")

    def calcular_dimensionamento(self, decisao_final):
        sinal = decisao_final.get("entrada", {}).get("sinal", "HOLD")
        
        if sinal in ["HOLD", "NEUTRO"]:
            return decisao_final
            
        preco_atual = decisao_final["entrada"]["preco_atual"]
        perda_maxima_sugerida = decisao_final["protecoes"]["stop_loss"]
        
        if preco_atual == 0 or perda_maxima_sugerida == 0:
            return decisao_final

        orcamento_risco = self.saldo_banca * self.risco_maximo
        distancia_ponto_parada = abs(preco_atual - perda_maxima_sugerida)
        
        tamanho_lote = 0.0
        if distancia_ponto_parada > 0:
            tamanho_lote = round(orcamento_risco / distancia_ponto_parada, 4)
            
        decisao_final["gestao_risco"] = {
            "banca_projetada": self.saldo_banca,
            "risco_dolares": round(orcamento_risco, 2),
            "tamanho_lote_ideal": tamanho_lote
        }
        
        self.logger.info(f"Matemática de Lote sugerida: {tamanho_lote} | Orcamento Risco: ${orcamento_risco}")
        return decisao_final
