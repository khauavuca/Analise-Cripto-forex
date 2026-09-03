import os
import threading
from datetime import datetime
import pandas as pd

from NovaDexyConnector import NovaDexyConnector
from ReviewAI import RevisaoIA
from Logger import iniciar_logger

class DecisaoCriptoForex:
    def __init__(self, entrada, expiracao, protecoes, indicadores):
        self.entrada = entrada
        self.expiracao = expiracao
        self.protecoes = protecoes
        self.indicadores = indicadores

    def converter_dicionario(self):
        return {
            "entrada": self.entrada,
            "expiracao": self.expiracao,
            "protecoes": self.protecoes,
            "indicadores": self.indicadores,
            "marca_tempo": datetime.now().isoformat()
        }

class AnalisadorMercado:
    """Extrai sinais técnicos e passa pela avaliação rígida da IA."""

    def __init__(self):
        self.logger = iniciar_logger("AnalisadorMercado")
        self.conector = NovaDexyConnector()
        self.ia_revisao = RevisaoIA()
        self._trava_execucao = threading.Lock()

    def obter_preco(self):
        return self.conector.obter_preco_atual()

    def obter_historico(self, limite=200, intervalo="ONE_HOU"):
        return self.conector.obter_velas_historicas(limite=limite, intervalo=intervalo)

    def processar_indicadores(self, dataframe_velas):
        """Processa suportes, resistências e médias matemáticas."""
        resultado = {}
        try:
            if dataframe_velas is None or len(dataframe_velas) < 50:
                return {"erro": "Falta de dados"}

            fechamentos = dataframe_velas["fechamento"]

            resultado["media_simples_20"] = float(fechamentos.rolling(20).mean().iloc[-1])
            resultado["media_simples_50"] = float(fechamentos.rolling(50).mean().iloc[-1])
            resultado["media_simples_200"] = float(fechamentos.rolling(200).mean().iloc[-1])

            diferenca = fechamentos.diff()
            ganho = (diferenca.where(diferenca > 0, 0)).rolling(14).mean()
            perda = (-diferenca.where(diferenca < 0, 0)).rolling(14).mean()
            forca_relativa = ganho / (perda + 1e-10)
            resultado["rsi"] = float(100 - (100 / (1 + forca_relativa.iloc[-1])))

            media_rapida = fechamentos.ewm(span=12, adjust=False).mean()
            media_lenta = fechamentos.ewm(span=26, adjust=False).mean()
            linha_macd = media_rapida - media_lenta
            linha_sinal_macd = linha_macd.ewm(span=9, adjust=False).mean()
            resultado["macd"] = float(linha_macd.iloc[-1])
            resultado["sinal_macd"] = float(linha_sinal_macd.iloc[-1])

            intervalo_alto_baixo = dataframe_velas["maxima"] - dataframe_velas["minima"]
            alto_fecha_prev = (dataframe_velas["maxima"] - dataframe_velas["fechamento"].shift()).abs()
            baixo_fecha_prev = (dataframe_velas["minima"] - dataframe_velas["fechamento"].shift()).abs()
            volatilidade_absoluta = pd.concat([intervalo_alto_baixo, alto_fecha_prev, baixo_fecha_prev], axis=1).max(axis=1)
            resultado["atr"] = float(volatilidade_absoluta.rolling(14).mean().iloc[-1])

            velas_recentes = dataframe_velas.tail(50)
            resultado["suporte"] = float(velas_recentes["minima"].min())
            resultado["resistencia"] = float(velas_recentes["maxima"].max())

            if resultado["media_simples_20"] > resultado["media_simples_50"] > resultado["media_simples_200"]:
                resultado["tendencia"] = "ALTA"
            elif resultado["media_simples_20"] < resultado["media_simples_50"] < resultado["media_simples_200"]:
                resultado["tendencia"] = "BAIXA"
            else:
                resultado["tendencia"] = "LATERAL"

            return resultado
        except Exception as erro:
            self.logger.error(f"Erro em parâmetros: {erro}")
            return {"erro": str(erro)}

    def varredura_dupla(self):
        dataframe = self.obter_historico(limite=100)
        sensores = self.processar_indicadores(dataframe)

        if "erro" in sensores:
            self.logger.warning(f"Análise abortada: {sensores['erro']} - Tamanho do DataFrame: {len(dataframe) if dataframe is not None else 0}")
            return None

        preco_cotacao = self.obter_preco()
        
        sinal_cru = "NEUTRO"
        valor_rsi = sensores["rsi"]
        valor_macd = sensores["macd"]
        sinal_macd = sensores["sinal_macd"]
        
        if valor_rsi < 40 and valor_macd > sinal_macd and sensores["tendencia"] != "BAIXA":
            sinal_cru = "COMPRA"
        elif valor_rsi > 60 and valor_macd < sinal_macd and sensores["tendencia"] != "ALTA":
            sinal_cru = "VENDA"
            
        veredicto_ia = self.ia_revisao.validar_sinal(sinal_cru, sensores)
        sinal_impresso = sinal_cru if veredicto_ia == "FORTE" else "NEUTRO"
        
        self.logger.info(f"Sinal Matemático: {sinal_cru} | IA Validadora: {veredicto_ia} -> RESULTADO FINO: {sinal_impresso}")
            
        relatorio_entrada = {
            "sinal": sinal_impresso,
            "aprovacao_ia": veredicto_ia,
            "preco_atual": round(preco_cotacao, 2)
        }
        
        relatorio_expiracao = {
            "timeframe_base": "1h",
            "duracao_esperada": "4 horas"
        }
        
        matriz_protecao = {"stop_loss": 0.0, "take_profit": 0.0}
        
        if sinal_impresso == "COMPRA":
            matriz_protecao["stop_loss"] = round(sensores["suporte"] - (sensores["atr"] * 0.5), 2)
            matriz_protecao["take_profit"] = round(sensores["resistencia"], 2)
        elif sinal_impresso == "VENDA":
            matriz_protecao["stop_loss"] = round(sensores["resistencia"] + (sensores["atr"] * 0.5), 2)
            matriz_protecao["take_profit"] = round(sensores["suporte"], 2)

        veredicto = DecisaoCriptoForex(relatorio_entrada, relatorio_expiracao, matriz_protecao, sensores)
        return veredicto.converter_dicionario()
