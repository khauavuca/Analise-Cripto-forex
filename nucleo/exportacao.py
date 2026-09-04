"""Observacoes em JSONL, para coleta que roda no GitHub Actions.

Por que texto e nao o banco: no Actions cada execucao grava o resultado de volta
no repositorio. Commitar um SQLite - arquivo binario - daria conflito a cada
execucao e inflaria o historico do Git com uma copia inteira do banco por vez.
JSONL resolve os dois: uma linha por observacao, append puro, e o diff mostra
exatamente o que entrou.

De quebra vira historico versionado: da para reconstruir o que o sistema via em
qualquer instante passado, que e o que um treino de modelo precisa para nao
aprender com dado que so existiu depois.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .dados.armazenamento import Armazenamento

CAMPOS = (
    "corretora", "par", "timeframe", "estrategia", "vela_ms",
    "abertura", "maxima", "minima", "fechamento", "volume",
    "direcao", "forca", "stop", "alvo", "motivo",
)


def _limpar(valor):
    """Converte tipos de numpy e pandas para algo que o json aceite."""
    if valor is None:
        return None
    if isinstance(valor, (str, bool, int)):
        return valor
    try:
        if pd.isna(valor):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(valor, "item"):
        return valor.item()
    if isinstance(valor, float):
        return valor
    return str(valor)


def caminho_do_mes(pasta: str | Path, momento: datetime | None = None) -> Path:
    """Um arquivo por mes: mantem cada um em tamanho tratavel."""
    quando = momento or datetime.now(timezone.utc)
    destino = Path(pasta)
    destino.mkdir(parents=True, exist_ok=True)
    return destino / f"observacoes-{quando:%Y-%m}.jsonl"


class EscritorJsonl:
    """Grava observacoes novas, ignorando as que ja estao no arquivo.

    A deduplicacao precisa morar aqui, e nao no banco, por causa de como o
    GitHub Actions funciona: cada execucao comeca com um banco vazio, entao
    toda vela pareceria nova e o arquivo encheria de repeticao. O arquivo do
    mes e a unica memoria que atravessa execucoes.
    """

    def __init__(self, pasta: str | Path, momento: datetime | None = None) -> None:
        self.caminho = caminho_do_mes(pasta, momento)
        self.vistas = chaves_existentes(self.caminho)
        self.gravadas = 0
        self.repetidas = 0

    def __call__(self, registro: dict) -> bool:
        chave = _chave(registro)
        if chave in self.vistas:
            self.repetidas += 1
            return False
        escrever(self.caminho, registro)
        self.vistas.add(chave)
        self.gravadas += 1
        return True


def _chave(registro: dict) -> tuple:
    return (
        registro.get("par"),
        registro.get("timeframe"),
        registro.get("estrategia"),
        str(registro.get("vela")),
    )


def chaves_existentes(caminho: Path) -> set[tuple]:
    """Le o arquivo do mes e devolve o que ja foi gravado."""
    if not Path(caminho).exists():
        return set()
    vistas = set()
    with open(caminho, encoding="utf-8") as entrada:
        for texto in entrada:
            texto = texto.strip()
            if not texto:
                continue
            try:
                vistas.add(_chave(json.loads(texto)))
            except json.JSONDecodeError:
                continue
    return vistas


def escrever(caminho: Path, registro: dict) -> None:
    """Acrescenta uma observacao. Append puro, uma linha, sem reescrever nada."""
    linha = {
        "registrado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "par": registro.get("par"),
        "timeframe": registro.get("timeframe"),
        "estrategia": registro.get("estrategia"),
        "vela": str(registro.get("vela")),
        "fechamento": _limpar(registro.get("fechamento")),
        "direcao": _limpar(registro.get("direcao")),
        "forca": _limpar(registro.get("forca")),
        "indicadores": {
            nome: _limpar(valor)
            for nome, valor in (registro.get("indicadores") or {}).items()
        },
    }
    with open(caminho, "a", encoding="utf-8") as saida:
        saida.write(json.dumps(linha, ensure_ascii=False) + "\n")


def ler(caminhos: list[str | Path]) -> pd.DataFrame:
    """Carrega um ou mais arquivos JSONL num quadro."""
    linhas = []
    for caminho in caminhos:
        arquivo = Path(caminho)
        if not arquivo.exists():
            continue
        with open(arquivo, encoding="utf-8") as entrada:
            for numero, texto in enumerate(entrada, 1):
                texto = texto.strip()
                if not texto:
                    continue
                try:
                    linhas.append(json.loads(texto))
                except json.JSONDecodeError:
                    # Uma linha truncada - execucao interrompida no meio da
                    # escrita - nao pode derrubar a leitura das outras milhares.
                    print(f"  ! linha {numero} de {arquivo.name} ilegivel, pulando")
    return pd.DataFrame(linhas)


def importar(armazenamento: Armazenamento, caminhos: list[str | Path]) -> int:
    """Leva as observacoes do JSONL para o banco, para analisar localmente."""
    quadro = ler(caminhos)
    if quadro.empty:
        return 0

    gravadas = 0
    for linha in quadro.itertuples():
        momento = pd.Timestamp(linha.vela)
        if momento.tzinfo is None:
            momento = momento.tz_localize("UTC")
        indicadores = getattr(linha, "indicadores", {}) or {}
        nova = armazenamento.registrar_observacao(
            corretora=indicadores.get("corretora", "binance"),
            par=linha.par,
            timeframe=linha.timeframe,
            estrategia=linha.estrategia,
            vela_ms=int(momento.timestamp() * 1000),
            vela={"fechamento": linha.fechamento},
            sinal={"direcao": linha.direcao, "forca": linha.forca},
            indicadores=indicadores,
        )
        gravadas += int(nova)
    return gravadas
