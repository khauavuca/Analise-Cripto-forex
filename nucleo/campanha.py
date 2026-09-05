"""Campanha de teste: cada setup e um trader com a propria banca, de mentira, no mercado de verdade.

Cada execucao **refaz a campanha do zero** a partir das velas reais entre o
inicio e agora: roda cada setup, passa os trades pela carteira e produz o
relatorio. Nao ha estado guardado entre execucoes para dar errado - se o
processo cair, a proxima execucao chega ao mesmo resultado. E como o motor ja
provou que vela a vela da o mesmo que de uma vez (`testes/test_replay.py`), a
campanha e, por construcao, identica ao que o backtest teria feito.

O que a torna "para frente": so contam operacoes cujo sinal nasceu DEPOIS do
inicio da campanha, e os setups foram congelados antes dela. As velas
anteriores ao inicio entram apenas para aquecer indicadores.

O relatorio e escrito para quem nao e do ramo. Quem quiser as metricas
completas tem `rastrear` e `backtest`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import pandas as pd

from .backtest.motor import MOTIVO_FIM, ConfigExecucao, ModeloCustos, executar
from .dados.carregador import carregar
from .dados.provedor import duracao_ms
from .estrategias.base import Estrategia
from .risco.carteira import RegrasCarteira, ResultadoCarteira, simular_carteira

MINIMO_PARA_OPINAR = 30


@dataclass(frozen=True)
class ConfigCampanha:
    inicio: datetime
    fim: datetime
    banca: float = 500.0
    moeda: str = "BRL"
    pares: tuple[str, ...] = ("BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT")
    timeframes: tuple[str, ...] = ("1h", "4h")
    regras: RegrasCarteira = field(default_factory=RegrasCarteira)
    custos: ModeloCustos = field(default_factory=ModeloCustos)
    execucao: ConfigExecucao = field(default_factory=ConfigExecucao)


@dataclass
class Trader:
    nome: str
    carteira: ResultadoCarteira
    fechadas: pd.DataFrame
    abertas: pd.DataFrame

    @property
    def saldo(self) -> float:
        return self.carteira.saldo_final

    @property
    def ganhas(self) -> int:
        if self.carteira.fechamentos.empty:
            return 0
        return int((self.carteira.fechamentos["resultado"] > 0).sum())

    @property
    def perdidas(self) -> int:
        return int(len(self.carteira.fechamentos)) - self.ganhas


@dataclass
class ResultadoCampanha:
    config: ConfigCampanha
    gerado_em: datetime
    ultima_vela: pd.Timestamp | None
    velas_no_periodo: int
    traders: list[Trader]

    def ranking(self) -> pd.DataFrame:
        linhas = []
        for t in self.traders:
            operacoes = t.ganhas + t.perdidas
            linhas.append(
                {
                    "trader": t.nome,
                    "banca_inicial": self.config.banca,
                    "banca_atual": round(t.saldo, 2),
                    "resultado": round(t.saldo - self.config.banca, 2),
                    "variacao": t.saldo / self.config.banca - 1,
                    "operacoes": operacoes,
                    "ganhas": t.ganhas,
                    "perdidas": t.perdidas,
                    "em_aberto": int(len(t.abertas)),
                    "maior_queda": t.carteira.rebaixamento_maximo,
                    "recusadas": int(sum(t.carteira.recusas.values())),
                }
            )
        quadro = pd.DataFrame(linhas)
        if quadro.empty:
            return quadro
        return quadro.sort_values("banca_atual", ascending=False).reset_index(drop=True)


def avaliar(
    quadros: dict[tuple[str, str], pd.DataFrame],
    estrategias: list[Estrategia],
    config: ConfigCampanha,
    agora: datetime | None = None,
) -> ResultadoCampanha:
    """Roda a campanha sobre velas ja carregadas. Puro: sem rede, sem banco."""
    agora = agora or datetime.now(timezone.utc)
    inicio = pd.Timestamp(config.inicio)
    fim = pd.Timestamp(config.fim)

    recortados = {
        chave: quadro[quadro.index < fim] for chave, quadro in quadros.items() if not quadro.empty
    }
    ultima_vela = max((q.index[-1] for q in recortados.values()), default=None)
    velas_no_periodo = sum(int((q.index >= inicio).sum()) for q in recortados.values())

    traders = []
    for estrategia in estrategias:
        partes = []
        for (par, timeframe), quadro in recortados.items():
            resultado = executar(
                quadro, estrategia.gerar_sinais(quadro), config.custos, config.execucao
            )
            trades = resultado.trades.copy()
            if trades.empty:
                continue
            trades["par"] = par
            trades["timeframe"] = timeframe
            trades["estrategia"] = estrategia.nome
            # So conta o que nasceu dentro da campanha. As velas de antes
            # existem para aquecer indicadores, nao para operar.
            partes.append(trades[trades["entrada"] >= inicio])

        todos = pd.concat(partes, ignore_index=True) if partes else pd.DataFrame(
            columns=["motivo_saida", "entrada"]
        )
        fechadas = todos[todos["motivo_saida"] != MOTIVO_FIM] if not todos.empty else todos
        abertas = todos[todos["motivo_saida"] == MOTIVO_FIM] if not todos.empty else todos

        regras = RegrasCarteira(
            **{**config.regras.__dict__, "saldo_inicial": config.banca, "moeda": config.moeda}
        )
        carteira = simular_carteira(todos, regras) if not todos.empty else simular_carteira(
            pd.DataFrame(), regras
        )
        traders.append(Trader(estrategia.nome, carteira, fechadas, abertas))

    traders.sort(key=lambda t: t.saldo, reverse=True)
    return ResultadoCampanha(config, agora, ultima_vela, velas_no_periodo, traders)


def rodar(
    config: ConfigCampanha,
    estrategias: list[Estrategia],
    provedor,
    armazenamento,
    usar_rede: bool = True,
) -> ResultadoCampanha:
    """Carrega as velas reais e avalia."""
    agora = datetime.now(timezone.utc)
    fim_efetivo = min(config.fim, agora)
    aquecimento = max(e.barras_de_aquecimento() for e in estrategias) + 5

    quadros = {}
    for par in config.pares:
        for timeframe in config.timeframes:
            inicio_carga = config.inicio - timedelta(
                milliseconds=duracao_ms(timeframe) * aquecimento
            )
            quadros[(par, timeframe)] = carregar(
                par, timeframe, inicio_carga, fim_efetivo,
                provedor=provedor, armazenamento=armazenamento,
                barras_aquecimento=0, usar_rede=usar_rede,
            )
    return avaliar(quadros, estrategias, config, agora)


def _moeda(config: ConfigCampanha, valor: float) -> str:
    simbolo = "R$" if config.moeda.upper() == "BRL" else config.moeda
    texto = f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{simbolo} {texto}"


def relatorio_simples(resultado: ResultadoCampanha) -> str:
    """Em portugues claro, para quem nao e do ramo."""
    c = resultado.config
    linhas = [
        "CAMPANHA DE TESTE - dinheiro de mentira, mercado de verdade",
        f"Periodo: {c.inicio:%d/%m/%Y} ate {c.fim - timedelta(seconds=1):%d/%m/%Y} | "
        f"atualizado em {resultado.gerado_em:%d/%m %H:%M} UTC",
    ]
    if resultado.ultima_vela is not None:
        linhas.append(f"Ultima vela considerada: {resultado.ultima_vela:%d/%m %H:%M} UTC")
    linhas += [
        f"Cada trader comecou com {_moeda(c, c.banca)} e gerencia a propria banca "
        f"com as mesmas regras: arrisca {c.regras.risco_por_trade:.0%} por operacao, "
        f"no maximo {c.regras.max_posicoes} operacoes ao mesmo tempo, e para o dia se "
        f"perder {c.regras.perda_diaria_maxima:.0%}.",
        f"Pares: {', '.join(c.pares)} | graficos: {', '.join(c.timeframes)}",
        "",
    ]

    ranking = resultado.ranking()
    if ranking.empty:
        linhas.append("Nenhum trader configurado.")
        return "\n".join(linhas)

    for posicao, r in enumerate(ranking.itertuples(), 1):
        sinal = "+" if r.resultado >= 0 else "-"
        linhas.append(
            f"{posicao:>2}o  {r.trader:<24} {_moeda(c, r.banca_atual):>14}  "
            f"({sinal}{_moeda(c, abs(r.resultado))}, {r.variacao:+.1%})  "
            f"{r.operacoes} operacoes: {r.ganhas} ganhas, {r.perdidas} perdidas"
            + (f" | {r.em_aberto} em aberto" if r.em_aberto else "")
            + (f" | pior momento {r.maior_queda:.1%}" if r.maior_queda < 0 else "")
        )

    maximo = int(ranking["operacoes"].max())
    linhas.append("")
    if maximo < MINIMO_PARA_OPINAR:
        linhas.append(
            f"AINDA E CEDO: nenhum trader passou de {MINIMO_PARA_OPINAR} operacoes fechadas "
            f"(o maximo foi {maximo}). Com poucas operacoes a ordem acima e sorte, nao "
            f"habilidade. A campanha existe para acumular operacoes; a ordem so vale quando "
            f"a amostra crescer."
        )
    else:
        linhas.append(
            "Com mais de 30 operacoes a ordem comeca a significar alguma coisa - "
            "mas continua sendo uma semana, e uma semana e um regime de mercado so."
        )
    linhas += [
        "",
        "Como ler: 'ganha' e operacao que fechou com lucro depois de taxas; 'em aberto' e "
        "operacao que ainda nao bateu o alvo nem o stop; 'pior momento' e quanto a banca "
        "chegou a cair do topo. Recusadas sao sinais que as regras de banca barraram.",
        "Nenhuma ordem real foi enviada.",
    ]
    return "\n".join(linhas)


def para_json(resultado: ResultadoCampanha) -> dict:
    c = resultado.config
    return {
        "gerado_em": resultado.gerado_em.isoformat(timespec="seconds"),
        "inicio": c.inicio.isoformat(),
        "fim": c.fim.isoformat(),
        "banca_inicial": c.banca,
        "moeda": c.moeda,
        "pares": list(c.pares),
        "timeframes": list(c.timeframes),
        "ultima_vela": None if resultado.ultima_vela is None else resultado.ultima_vela.isoformat(),
        "velas_no_periodo": resultado.velas_no_periodo,
        "ranking": resultado.ranking().to_dict(orient="records"),
        "traders": {
            t.nome: {
                "banca_atual": t.saldo,
                "fechadas": t.carteira.fechamentos.to_dict(orient="records"),
                "abertas": t.abertas[
                    [c for c in ("par", "timeframe", "direcao", "entrada", "preco_entrada", "stop", "alvo") if c in t.abertas.columns]
                ].to_dict(orient="records") if not t.abertas.empty else [],
                "recusadas": dict(t.carteira.recusas),
            }
            for t in resultado.traders
        },
    }
