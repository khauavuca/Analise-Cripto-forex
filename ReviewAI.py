import os
import joblib
import pandas as pd
from Logger import iniciar_logger

try:
    from sklearn.ensemble import RandomForestClassifier
except ImportError:
    RandomForestClassifier = None

class RevisaoIA:
    """
    IA de Revisão (Dupla Validação).
    Avalia sinais técnicos e os classifica como FORTE ou FRACO.
    """
    def __init__(self, caminho_modelo="modelo_revisao_ia.pkl"):
        self.logger = iniciar_logger("RevisaoIA")
        self.caminho_modelo = caminho_modelo
        self.modelo = None
        self.treinado = False
        self._carregar_ou_criar_modelo()

    def _carregar_ou_criar_modelo(self):
        if RandomForestClassifier is None:
            self.logger.warning("Scikit-Learn ausente. Operando em modo de regras lógicas.")
            return

        if os.path.exists(self.caminho_modelo):
            try:
                self.modelo = joblib.load(self.caminho_modelo)
                self.treinado = True
                self.logger.info("Modelo de IA carregado com sucesso.")
            except Exception as erro:
                self.logger.error(f"Erro ao carregar IA: {erro}")
                self._construir_novo_modelo()
        else:
            self._construir_novo_modelo()

    def _construir_novo_modelo(self):
        if RandomForestClassifier is None:
            return
            
        self.modelo = RandomForestClassifier(n_estimators=100, max_depth=5, random_state=42)
        self.logger.info("Novo modelo Floresta Aleatória criado.")

    def treinar_no_historico(self, dataframe_features: pd.DataFrame, categorias: pd.Series):
        if self.modelo is None:
            return
            
        try:
            self.modelo.fit(dataframe_features, categorias)
            joblib.dump(self.modelo, self.caminho_modelo)
            self.treinado = True
            self.logger.info("IA de Revisão treinada com sucesso.")
        except Exception as erro:
            self.logger.error(f"Falha ao treinar IA: {erro}")

    def validar_sinal(self, sinal_preliminar: str, indicadores: dict) -> str:
        """Retorna 'FORTE' ou 'FRACO' cruzando o sinal com os indicadores."""
        if sinal_preliminar in ["HOLD", "NEUTRO"]:
            return "FRACO"

        caracteristicas = {
            "rsi": indicadores.get("rsi", 50),
            "macd": indicadores.get("macd", 0),
            "percentual_atr": indicadores.get("atr", 0) / (indicadores.get("suporte", 1)) * 100
        }

        if not self.treinado or self.modelo is None:
            return self._validacao_logica_reserva(sinal_preliminar, caracteristicas)

        try:
            # Transformação simples
            features_teste = pd.DataFrame([caracteristicas])
            probabilidades = self.modelo.predict_proba(features_teste)[0]
            probabilidade_acerto = probabilidades[1]
            
            return "FORTE" if probabilidade_acerto > 0.60 else "FRACO"
            
        except Exception as erro:
            self.logger.error(f"Erro na dupla validação: {erro}")
            return self._validacao_logica_reserva(sinal_preliminar, caracteristicas)

    def _validacao_logica_reserva(self, sinal_tecnico: str, caracteristicas: dict) -> str:
        rsi = caracteristicas.get("rsi", 50)
        macd = caracteristicas.get("macd", 0)
        
        if sinal_tecnico == "COMPRA" and rsi < 65 and macd > 0:
            return "FORTE"
        elif sinal_tecnico == "VENDA" and rsi > 35 and macd < 0:
            return "FORTE"
                
        return "FRACO"
