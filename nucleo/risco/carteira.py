"""Gestao de banca com varias posicoes ao mesmo tempo.

A simulacao anterior compunha os trades em fila, um depois do outro, e isso
esconde um problema real: quando tres setups disparam em tres pares na mesma
hora, a banca esta tres vezes exposta. Aqui as posicoes coexistem, o capital e
compartilhado, e as regras que um operador profissional usa para nao quebrar
sao aplicadas na ordem em que os eventos acontecem:

- **teto de posicoes** simultaneas e de posicoes por par
- **teto de exposicao**: a soma do que esta no mercado nao passa da banca
- **perda diaria maxima**: bateu, para de abrir ate o dia virar (kill switch)
- **pausa apos sequencia de perdas**: quatro stops seguidos e um sinal de que
  o mercado nao esta no regime do setup; os proximos sinais sao pulados

Nada aqui envia ordem. E contabilidade: dado um sinal, a carteira diz se
entraria, com quanto, e por que nao.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .. import tempo


@dataclass(frozen=True)
class RegrasCarteira:
    saldo_inicial: float = 100.0
    risco_por_trade: float = 0.02
    max_posicoes: int = 3
    max_por_par: int = 1
    exposicao_maxima: float = 1.0
    perda_diaria_maxima: float = 0.06
    perdas_seguidas_para_pausa: int = 4
    sinais_de_pausa: int = 3
    valor_minimo_ordem: float = 1.0
    moeda: str = "USDT"
    # A perda diaria zera a meia-noite DESTE fuso, nao do UTC: e uma regra de
    # quem opera, e quem opera esta no Brasil.
    fuso: str = field(default_factory=lambda: tempo.fuso().key)


@dataclass
class Posicao:
    chave: object
    par: str
    estrategia: str
    direcao: int
    entrada: pd.Timestamp
    valor: float
    risco: float


@dataclass
class Recusa:
    momento: pd.Timestamp
    par: str
    estrategia: str
    motivo: str


class Carteira:
    def __init__(self, regras: RegrasCarteira | None = None) -> None:
        self.regras = regras or RegrasCarteira()
        self._fuso = tempo.fuso(self.regras.fuso)
        self.saldo = self.regras.saldo_inicial
        self.posicoes: dict[object, Posicao] = {}
        self.dia_atual = None
        self.saldo_inicio_dia = self.saldo
        self.perda_do_dia = 0.0
        self.perdas_seguidas = 0
        self.sinais_em_pausa = 0
        self.recusas: list[Recusa] = []
        self.fechamentos: list[dict] = []
        self.pico = self.saldo
        self.rebaixamento_maximo = 0.0

    # ------------------------------------------------------------ estado

    @property
    def exposicao(self) -> float:
        return sum(p.valor for p in self.posicoes.values())

    def _virar_dia(self, momento: pd.Timestamp) -> None:
        dia = tempo.dia(momento, self._fuso)
        if dia != self.dia_atual:
            self.dia_atual = dia
            self.saldo_inicio_dia = self.saldo
            self.perda_do_dia = 0.0

    def _limite_diario_batido(self) -> bool:
        limite = self.saldo_inicio_dia * self.regras.perda_diaria_maxima
        return self.perda_do_dia >= limite > 0

    # ------------------------------------------------------------ regras

    def motivo_para_recusar(self, par: str, momento: pd.Timestamp) -> str | None:
        """None se pode abrir; senao, o motivo."""
        self._virar_dia(momento)
        r = self.regras
        if self.saldo <= 0:
            return "banca zerada"
        if self._limite_diario_batido():
            return "perda diaria maxima"
        if self.sinais_em_pausa > 0:
            self.sinais_em_pausa -= 1
            return "pausa apos sequencia de perdas"
        if len(self.posicoes) >= r.max_posicoes:
            return "teto de posicoes"
        if sum(1 for p in self.posicoes.values() if p.par == par) >= r.max_por_par:
            return "ja posicionado no par"
        if self.exposicao >= self.saldo * r.exposicao_maxima:
            return "teto de exposicao"
        return None

    def dimensionar(self, preco_entrada: float, stop: float) -> float:
        """Valor da ordem: risco fixo sobre a banca atual, limitado pelo espaco."""
        distancia = abs(preco_entrada - stop) / preco_entrada
        if not np.isfinite(distancia) or distancia <= 0:
            return 0.0
        orcamento = self.saldo * self.regras.risco_por_trade
        espaco = self.saldo * self.regras.exposicao_maxima - self.exposicao
        return max(0.0, min(orcamento / distancia, espaco))

    def abrir(
        self, chave, par: str, estrategia: str, direcao: int,
        momento: pd.Timestamp, preco_entrada: float, stop: float,
    ) -> Posicao | None:
        motivo = self.motivo_para_recusar(par, momento)
        if motivo is None:
            valor = self.dimensionar(preco_entrada, stop)
            if valor < self.regras.valor_minimo_ordem:
                motivo = "abaixo da ordem minima"
        if motivo is not None:
            self.recusas.append(Recusa(momento, par, estrategia, motivo))
            return None

        posicao = Posicao(
            chave=chave, par=par, estrategia=estrategia, direcao=direcao,
            entrada=momento, valor=valor,
            risco=valor * abs(preco_entrada - stop) / preco_entrada,
        )
        self.posicoes[chave] = posicao
        return posicao

    def fechar(self, chave, momento: pd.Timestamp, retorno_liquido_pct: float) -> float:
        posicao = self.posicoes.pop(chave, None)
        if posicao is None:
            return 0.0
        self._virar_dia(momento)
        resultado = posicao.valor * retorno_liquido_pct
        self.saldo += resultado

        if resultado < 0:
            self.perda_do_dia += -resultado
            self.perdas_seguidas += 1
            if self.perdas_seguidas >= self.regras.perdas_seguidas_para_pausa:
                self.sinais_em_pausa = self.regras.sinais_de_pausa
                self.perdas_seguidas = 0
        else:
            self.perdas_seguidas = 0

        self.pico = max(self.pico, self.saldo)
        self.rebaixamento_maximo = min(self.rebaixamento_maximo, self.saldo / self.pico - 1)
        self.fechamentos.append(
            {
                "momento": momento, "par": posicao.par, "estrategia": posicao.estrategia,
                "valor": posicao.valor, "resultado": resultado, "saldo": self.saldo,
            }
        )
        return resultado


@dataclass
class ResultadoCarteira:
    regras: RegrasCarteira
    saldo_final: float
    executados: int
    recusas: Counter
    rebaixamento_maximo: float
    curva: pd.Series
    fechamentos: pd.DataFrame
    detalhe_recusas: pd.DataFrame = field(default_factory=pd.DataFrame)

    @property
    def retorno(self) -> float:
        return self.saldo_final / self.regras.saldo_inicial - 1


COLUNAS_NECESSARIAS = {
    "par", "estrategia", "direcao", "entrada", "saida", "preco_entrada",
    "stop", "retorno_liquido_pct", "motivo_saida",
}


def simular_carteira(trades: pd.DataFrame, regras: RegrasCarteira | None = None) -> ResultadoCarteira:
    """Passa os trades de todos os setups e pares pela mesma banca, em ordem."""
    regras = regras or RegrasCarteira()
    carteira = Carteira(regras)

    if trades is None or trades.empty:
        return ResultadoCarteira(regras, carteira.saldo, 0, Counter(), 0.0,
                                 pd.Series(dtype=float), pd.DataFrame())

    faltando = COLUNAS_NECESSARIAS - set(trades.columns)
    if faltando:
        raise ValueError(f"Faltam colunas nos trades: {sorted(faltando)}")

    fechados = trades[trades["motivo_saida"] != "FIM_DADOS"].reset_index(drop=True)

    # Eventos em ordem de tempo. No mesmo instante, fechamentos vem antes de
    # aberturas: o capital que acabou de ser liberado ja pode ser usado. A
    # excecao e o trade que abre e fecha na MESMA barra (estopado na barra de
    # entrada): o fechamento dele precisa vir depois da propria abertura, senao
    # a carteira tenta fechar uma posicao que ainda nao existe e a deixa aberta
    # para sempre.
    eventos = []
    for i, t in fechados.iterrows():
        entrada, saida = pd.Timestamp(t["entrada"]), pd.Timestamp(t["saida"])
        eventos.append((entrada, 1, i, "abrir"))
        eventos.append((saida, 0 if saida > entrada else 2, i, "fechar"))
    eventos.sort(key=lambda e: (e[0], e[1], e[2]))

    curva = {}
    for momento, _, i, tipo in eventos:
        t = fechados.iloc[i]
        if tipo == "abrir":
            carteira.abrir(
                chave=i, par=t["par"], estrategia=t["estrategia"], direcao=int(t["direcao"]),
                momento=momento, preco_entrada=float(t["preco_entrada"]), stop=float(t["stop"]),
            )
        else:
            carteira.fechar(i, momento, float(t["retorno_liquido_pct"]))
        curva[momento] = carteira.saldo

    return ResultadoCarteira(
        regras=regras,
        saldo_final=carteira.saldo,
        executados=len(carteira.fechamentos),
        recusas=Counter(r.motivo for r in carteira.recusas),
        rebaixamento_maximo=carteira.rebaixamento_maximo,
        curva=pd.Series(curva).sort_index(),
        fechamentos=pd.DataFrame(carteira.fechamentos),
        detalhe_recusas=pd.DataFrame([r.__dict__ for r in carteira.recusas]),
    )


def relatorio(resultado: ResultadoCarteira) -> str:
    r = resultado.regras
    linhas = [
        f"  banca inicial        {r.moeda} {r.saldo_inicial:,.2f}",
        f"  saldo final          {r.moeda} {resultado.saldo_final:,.2f}  ({resultado.retorno:+.1%})",
        f"  trades executados    {resultado.executados}",
        f"  rebaixamento maximo  {resultado.rebaixamento_maximo:.1%}",
        f"  regras: ate {r.max_posicoes} posicoes, {r.max_por_par} por par, exposicao "
        f"{r.exposicao_maxima:.0%}, perda diaria {r.perda_diaria_maxima:.0%}, pausa apos "
        f"{r.perdas_seguidas_para_pausa} perdas",
    ]
    total_recusas = sum(resultado.recusas.values())
    if total_recusas:
        linhas.append(f"  sinais recusados     {total_recusas}")
        for motivo, n in resultado.recusas.most_common():
            linhas.append(f"    {n:>5}  {motivo}")
    return "\n".join(linhas)
