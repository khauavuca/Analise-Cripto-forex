import os
import time
import hmac
import hashlib
import json
from urllib.parse import urlencode
import requests
import pandas as pd
from datetime import datetime

from Logger import iniciar_logger

class NovaDexyConnector:
    """
    Conector integrado com a documentação oficial da NovaDAX (NovaDexy).
    Substitui a dependência da Binance e usa apenas Português.
    - Login via Chave de Acesso e Chave Secreta.
    - Captura de velas (klines) e preços.
    - Envio de ordens com assinatura (HMAC-SHA256).
    """
    def __init__(self):
        self.logger = iniciar_logger("NovaDexy")
        self.chave_api = os.getenv("NOVADEXY_API_KEY", "")
        self.chave_secreta = os.getenv("NOVADEXY_API_SECRET", "")
        self.url_base = "https://api.novadax.com"
        self.sessao = requests.Session()
        
        if not self.chave_api or not self.chave_secreta:
            self.logger.warning("Chave de API ou Secreta ausentes no .env. Endpoints privados não funcionarão.")

    def _gerar_assinatura_get(self, caminho: str, parametros_query: dict, marca_tempo: str) -> str:
        string_query = ""
        if parametros_query:
            parametros_ordenados = sorted(parametros_query.items())
            string_query = urlencode(parametros_ordenados)
        
        texto_assinatura = f"GET\n{caminho}\n{string_query}\n{marca_tempo}"
        
        return hmac.new(
            self.chave_secreta.encode('utf-8'),
            texto_assinatura.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()

    def _gerar_assinatura_post(self, caminho: str, corpo: dict, marca_tempo: str) -> str:
        texto_corpo = json.dumps(corpo, separators=(',', ':')) if corpo else ""
        corpo_md5 = hashlib.md5(texto_corpo.encode('utf-8')).hexdigest() if texto_corpo else ""
        
        texto_assinatura = f"POST\n{caminho}\n{corpo_md5}\n{marca_tempo}"
        
        return hmac.new(
            self.chave_secreta.encode('utf-8'),
            texto_assinatura.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()

    def _obter_cabecalhos_autenticacao(self, assinatura: str, marca_tempo: str) -> dict:
        return {
            "X-Nova-Access-Key": self.chave_api,
            "X-Nova-Signature": assinatura,
            "X-Nova-Timestamp": marca_tempo,
            "Content-Type": "application/json"
        }

    def obter_preco_atual(self, par_moeda: str = "BTC_BRL") -> float:
        """Captura preço em tempo real."""
        try:
            caminho = "/v1/market/ticker"
            resposta = self.sessao.get(f"{self.url_base}{caminho}", params={"symbol": par_moeda}, timeout=10)
            dados = resposta.json()
            if dados.get("code") == "A10000":
                return float(dados["data"]["lastPrice"])
            else:
                self.logger.error(f"Erro NovaDAX: {dados.get('message')}")
                return 0.0
        except Exception as erro:
            self.logger.error(f"Erro ao obter preço: {erro}")
            return 0.0

    def obter_velas_historicas(self, par_moeda: str = "BTC_BRL", intervalo: str = "ONE_HOU", limite: int = 200) -> pd.DataFrame:
        """Captura velas históricas."""
        try:
            caminho = "/v1/market/kline/history"
            agora = int(time.time() * 1000)
            # Aproximação de tempo para pegar as velas (limite * horas * ms)
            # Se for ONE_HOU:
            inicio = agora - (limite * 60 * 60 * 1000)

            parametros = {
                "symbol": par_moeda,
                "unit": intervalo,
                "from": str(inicio),
                "to": str(agora)
            }
            self.logger.debug(f"Puxando Velas url: /v1/market/kline/history | Params: {parametros}")
            resposta = self.sessao.get(f"{self.url_base}{caminho}", params=parametros, timeout=10)
            dados = resposta.json()
            
            if dados.get("code") == "A10000":
                registros = dados["data"]
                self.logger.info(f"Tamanho de Klines obtidas da NovaDAX: {len(registros)}")
                # Colunas oficiais da corretora
                df = pd.DataFrame(registros, columns=["data_hora", "abertura", "maxima", "minima", "fechamento", "quantidade", "volume"])
                df["data_hora"] = pd.to_datetime(df["data_hora"], unit='ms')
                df = df.astype({"abertura": float, "maxima": float, "minima": float, "fechamento": float, "quantidade": float, "volume": float})
                return df
            else:
                self.logger.error(f"Erro histórico: {dados.get('message')}")
                return pd.DataFrame()
        except Exception as erro:
            self.logger.error(f"Erro ao obter velas históricas: {erro}")
            return pd.DataFrame()

    def enviar_ordem(self, par_moeda: str, lado: str, tipo_ordem: str = "MARKET", quantidade: float = 0, preco: float = None):
        """Envia ordem pública assinada digitalmente."""
        if not self.chave_api or not self.chave_secreta:
            self.logger.error("Falha: Chaves de API não configuradas.")
            return None
            
        marca_tempo = str(int(time.time() * 1000))
        caminho = "/v1/orders/create"
        
        corpo = {
            "symbol": par_moeda,
            "type": tipo_ordem.upper(),
            "side": lado.upper(),
        }
        
        if tipo_ordem.upper() == "LIMIT":
            corpo["price"] = str(preco)
            corpo["amount"] = str(quantidade)
        elif tipo_ordem.upper() == "MARKET":
            if lado.upper() == "BUY":
                corpo["value"] = str(quantidade) 
            else:
                corpo["amount"] = str(quantidade)
                
        assinatura = self._gerar_assinatura_post(caminho, corpo, marca_tempo)
        cabecalhos = self._obter_cabecalhos_autenticacao(assinatura, marca_tempo)
        
        try:
            self.logger.info(f"Enviando ordem: {corpo}")
            resposta = self.sessao.post(f"{self.url_base}{caminho}", json=corpo, headers=cabecalhos, timeout=10)
            dados_resposta = resposta.json()
            if dados_resposta.get("code") == "A10000":
                self.logger.info(f"Ordem colocada com sucesso: {dados_resposta['data']}")
                return dados_resposta["data"]
            else:
                self.logger.error(f"Erro ao colocar ordem: {dados_resposta}")
                return None
        except Exception as erro:
            self.logger.error(f"Exceção enviando ordem: {erro}")
            return None
