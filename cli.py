"""Linha de comando do analisador.

    python cli.py baixar      --par BTC/USDT --tf 1h --desde 2023-01-01
    python cli.py analisar    --par BTC/USDT --tf 4h
    python cli.py backtest    --par BTC/USDT --tf 1h --estrategia rsi_macd
    python cli.py comparar    --par BTC/USDT --tf 4h
    python cli.py validar     --par BTC/USDT --tf 1h --estrategia rsi_macd
    python cli.py walkforward --par BTC/USDT --tf 1h --estrategia rsi_macd

`baixar` e separado de `backtest` de proposito: o backtest roda do cache, sem
rede. Assim o resultado e reproduzivel e uma queda de conexao no meio nao
encurta a janela de dados em silencio, mudando as metricas sem aviso.
"""
from __future__ import annotations

import argparse
import json
import signal
import sys
from datetime import datetime, timedelta, timezone

import pandas as pd

from nucleo.backtest import metricas as met
from nucleo.backtest import validacao
from nucleo.backtest.motor import ConfigExecucao, ModeloCustos, executar
from nucleo.backtest.walkforward import executar_walkforward, grade_de_parametros
from nucleo.dados.armazenamento import Armazenamento
from nucleo.dados.carregador import carregar
from nucleo.dados.provedor_ccxt import ProvedorCCXT
from nucleo.estrategias.composta import EstrategiaComposta, matriz_correlacao_sinais
from nucleo.estrategias.cruzamento_ema import (
    EstrategiaCruzamentoEma,
    ParametrosCruzamentoEma,
)
from nucleo.estrategias.estrutura import (
    EstrategiaEstruturaMercado,
    ParametrosEstrutura,
)
from nucleo.estrategias.momento import EstrategiaMomentoTemporal, ParametrosMomento
from nucleo.estrategias.reversao import (
    EstrategiaDesvioVwap,
    EstrategiaReversaoBanda,
    ParametrosReversaoBanda,
    ParametrosVwap,
)
from nucleo.estrategias.rsi_macd import EstrategiaRsiMacd, ParametrosRsiMacd
from nucleo.estrategias.ruptura import (
    EstrategiaCompressaoVolatilidade,
    EstrategiaRupturaDonchian,
    ParametrosCompressao,
    ParametrosDonchian,
)
from nucleo.risco import gerenciador as risco


# Cada entrada e um setup mecanicamente distinto dos outros. A ideia nao e ter
# muitas estrategias, e ter poucas que discordem entre si - duas versoes do
# mesmo oscilador nao sao duas opinioes, sao a mesma com dois nomes.
REGISTRO = {
    "rsi_macd": (EstrategiaRsiMacd, ParametrosRsiMacd),
    "ema": (EstrategiaCruzamentoEma, ParametrosCruzamentoEma),
    "donchian": (EstrategiaRupturaDonchian, ParametrosDonchian),
    "compressao": (EstrategiaCompressaoVolatilidade, ParametrosCompressao),
    "momento": (EstrategiaMomentoTemporal, ParametrosMomento),
    "reversao_bb": (EstrategiaReversaoBanda, ParametrosReversaoBanda),
    "vwap": (EstrategiaDesvioVwap, ParametrosVwap),
    "estrutura": (EstrategiaEstruturaMercado, ParametrosEstrutura),
}

DESCRICOES = {
    "rsi_macd": "linha de base: RSI + MACD com filtro de tendencia",
    "ema": "cruzamento de medias exponenciais filtrado por ADX",
    "donchian": "rompimento de canal (Tartarugas / CTA)",
    "compressao": "squeeze: volatilidade comprimida se soltando",
    "momento": "momento temporal normalizado por volatilidade (TSMOM)",
    "reversao_bb": "reversao na banda de Bollinger em mercado lateral",
    "vwap": "desvio do VWAP ancorado, benchmark institucional",
    "estrutura": "rompimento de topo/fundo confirmado (price action)",
    "confluencia": "composicao ponderada de RSI+MACD com cruzamento de medias",
}

ESTRATEGIAS = tuple(REGISTRO) + ("confluencia",)


def construir(nome: str, **ajustes):
    nome = nome.strip()
    if nome == "confluencia":
        return EstrategiaComposta(
            [EstrategiaRsiMacd(), EstrategiaCruzamentoEma()], modo="ponderado", limiar=0.4
        )
    if nome not in REGISTRO:
        raise SystemExit(
            f"Estrategia desconhecida: {nome}. Disponiveis: {', '.join(ESTRATEGIAS)}"
        )
    classe, parametros = REGISTRO[nome]
    return classe(parametros(**ajustes))


def _contexto(args):
    provedor = ProvedorCCXT(args.corretora)
    armazenamento = Armazenamento(args.banco)
    return provedor, armazenamento


def _periodo(args) -> tuple[datetime, datetime | None]:
    inicio = (
        datetime.fromisoformat(args.desde).replace(tzinfo=timezone.utc)
        if getattr(args, "desde", None)
        else datetime.now(timezone.utc) - timedelta(days=args.dias)
    )
    fim = (
        datetime.fromisoformat(args.ate).replace(tzinfo=timezone.utc)
        if getattr(args, "ate", None)
        else None
    )
    return inicio, fim


def _custos(args) -> ModeloCustos:
    return ModeloCustos(taxa_por_lado=args.taxa, slippage_por_lado=args.slippage)


def _config(args) -> ConfigExecucao:
    return ConfigExecucao(
        max_barras_no_trade=args.max_barras,
        ambiguidade=args.ambiguidade,
        dimensionamento=args.dimensionamento,
        risco_por_trade=args.risco,
    )


def _dados(args, estrategia, provedor, armazenamento) -> pd.DataFrame:
    inicio, fim = _periodo(args)
    quadro = carregar(
        args.par,
        args.tf,
        inicio,
        fim,
        provedor=provedor,
        armazenamento=armazenamento,
        barras_aquecimento=estrategia.barras_de_aquecimento(),
        usar_rede=not getattr(args, "offline", False),
    )
    if quadro.empty:
        raise SystemExit(
            f"Sem velas de {args.par} {args.tf} no periodo. "
            f"Rode 'baixar' antes, ou tire --offline."
        )
    return quadro


# --------------------------------------------------------------------- baixar


def comando_baixar(args) -> int:
    provedor, armazenamento = _contexto(args)
    inicio, fim = _periodo(args)

    for par in args.par.split(","):
        par = par.strip()
        quadro = carregar(
            par, args.tf, inicio, fim, provedor=provedor, armazenamento=armazenamento
        )
        if quadro.empty:
            print(f"{par:<12} nenhuma vela no periodo")
            continue
        print(
            f"{par:<12} {len(quadro):>6} velas  "
            f"{quadro.index[0]:%Y-%m-%d %H:%M} .. {quadro.index[-1]:%Y-%m-%d %H:%M}  "
            f"(no banco: {armazenamento.contar_velas(provedor.nome, par, args.tf)})"
        )
    armazenamento.fechar()
    return 0


# ------------------------------------------------------------------- analisar


def comando_analisar(args) -> int:
    provedor, armazenamento = _contexto(args)
    estrategia = construir(args.estrategia)
    quadro = _dados(args, estrategia, provedor, armazenamento)
    sinais = estrategia.gerar_sinais(quadro)

    ultima = sinais.iloc[-1]
    momento = sinais.index[-1]
    rotulo = {1: "COMPRA", -1: "VENDA", 0: "SEM SINAL"}[int(ultima.direcao)]

    print(f"=== {args.par} {args.tf} | {estrategia.nome} ===")
    print(f"ultima vela fechada : {momento:%Y-%m-%d %H:%M} UTC")
    print(f"fechamento          : {quadro.fechamento.iloc[-1]:,.2f}")
    print(f"sinal               : {rotulo}")

    if int(ultima.direcao) == 0:
        recentes = sinais[sinais.direcao != 0].tail(1)
        if not recentes.empty:
            print(
                f"ultimo sinal        : "
                f"{'COMPRA' if recentes.direcao.iloc[0] > 0 else 'VENDA'} em "
                f"{recentes.index[0]:%Y-%m-%d %H:%M}"
            )
        armazenamento.fechar()
        return 0

    print(f"confianca           : {ultima.forca:.0%}  ({ultima.motivo})")
    print(f"stop                : {ultima.stop:,.2f}")
    print(f"alvo                : {ultima.alvo:,.2f}")

    entrada = float(quadro.fechamento.iloc[-1])
    tamanho = risco.dimensionar(entrada, float(ultima.stop), risco.config_do_ambiente())
    if tamanho.viavel:
        print(
            f"posicao sugerida    : {tamanho.quantidade:.6f} "
            f"(exposicao {tamanho.valor_exposto:,.2f}, "
            f"risco {tamanho.risco_em_moeda:,.2f}, "
            f"stop a {tamanho.distancia_stop_pct:.2%})"
        )
        if tamanho.observacao:
            print(f"                      {tamanho.observacao}")
    else:
        print(f"posicao sugerida    : inviavel - {tamanho.observacao}")

    print(
        "\nO sinal e uma leitura tecnica sobre a ultima vela fechada, nao uma "
        "recomendacao.\nAntes de agir, rode 'backtest' e 'validar' para ver "
        "quanto esta estrategia acerta."
    )
    armazenamento.fechar()
    return 0


# ------------------------------------------------------------------- backtest


def comando_backtest(args) -> int:
    provedor, armazenamento = _contexto(args)
    estrategia = construir(args.estrategia)
    quadro = _dados(args, estrategia, provedor, armazenamento)

    sinais = estrategia.gerar_sinais(quadro)
    resultado = executar(quadro, sinais, _custos(args), _config(args))
    metricas = met.calcular(resultado.trades, resultado.curva_capital, quadro, args.tf)

    titulo = f"{args.par} {args.tf} | {estrategia.nome}"
    print(met.formatar_relatorio(metricas, resultado.diagnosticos, titulo))

    if args.salvar:
        execucao = armazenamento.registrar_execucao(
            estrategia.nome,
            args.par,
            args.tf,
            str(quadro.index[0]),
            str(quadro.index[-1]),
            json.dumps({"estrategia": args.estrategia}),
            json.dumps({"taxa": args.taxa, "slippage": args.slippage}),
            json.dumps(metricas.para_dicionario(), default=str),
        )
        gravados = armazenamento.registrar_trades(
            execucao, estrategia.nome, args.par, args.tf, resultado.trades
        )
        print(
            f"\nExecucao #{execucao} gravada com {gravados} trades em "
            f"{armazenamento.caminho}."
        )

    armazenamento.fechar()
    return 0


# ------------------------------------------------------------------- comparar


def comando_comparar(args) -> int:
    provedor, armazenamento = _contexto(args)
    nomes = args.estrategias.split(",") if args.estrategias else list(ESTRATEGIAS)

    linhas = []
    quadro_referencia = None
    for nome in nomes:
        estrategia = construir(nome.strip())
        quadro = _dados(args, estrategia, provedor, armazenamento)
        quadro_referencia = quadro
        resultado = executar(
            quadro, estrategia.gerar_sinais(quadro), _custos(args), _config(args)
        )
        m = met.calcular(resultado.trades, resultado.curva_capital, quadro, args.tf)
        linhas.append(
            {
                "estrategia": estrategia.nome,
                "expectancia_R": round(m.expectancia_r, 3),
                "fator_lucro": round(m.fator_lucro, 2),
                "trades": m.n_trades,
                "acerto": f"{m.taxa_acerto:.0%} [{m.acerto_ic_baixo:.0%}-{m.acerto_ic_alto:.0%}]",
                "payoff": round(m.payoff, 2),
                "retorno": f"{m.retorno_total:+.1%}",
                "DD_max": f"{m.rebaixamento_maximo:.1%}",
                "ok": "sim" if m.amostra_suficiente else "AMOSTRA CURTA",
            }
        )

    if quadro_referencia is not None:
        buy_hold = met.retorno_comprar_e_segurar(quadro_referencia)
        linhas.append(
            {
                "estrategia": "comprar e segurar",
                "expectancia_R": "-", "fator_lucro": "-", "trades": 1,
                "acerto": "-", "payoff": "-",
                "retorno": f"{buy_hold:+.1%}", "DD_max": "-", "ok": "referencia",
            }
        )

    tabela = pd.DataFrame(linhas)
    print(f"=== {args.par} {args.tf} ===")
    # Expectancia vem primeiro e acerto no meio, de proposito: taxa de acerto
    # como primeira coluna convida a escolher a estrategia errada.
    print(tabela.to_string(index=False))

    if args.correlacao:
        estrategias = [construir(nome.strip()) for nome in nomes]
        print("\nCorrelacao entre os sinais (acima de 0,8 sao a mesma estrategia):")
        print(matriz_correlacao_sinais(estrategias, quadro_referencia).round(2).to_string())

    armazenamento.fechar()
    return 0


# -------------------------------------------------------------------- validar


def comando_validar(args) -> int:
    provedor, armazenamento = _contexto(args)
    estrategia = construir(args.estrategia)
    quadro = _dados(args, estrategia, provedor, armazenamento)
    sinais = estrategia.gerar_sinais(quadro)
    custos, config = _custos(args), _config(args)

    print(f"=== validacao: {args.par} {args.tf} | {estrategia.nome} ===\n")

    print("1. Sensibilidade ao atraso de execucao")
    print(
        validacao.sensibilidade_atraso(quadro, sinais, custos, config).to_string(index=False)
    )
    print(
        "   Vantagem real degrada devagar. Se despenca de 0 para 1 e some em 2,\n"
        "   o resultado vinha de olhar o futuro, nao do mercado.\n"
    )

    print("2. Sensibilidade ao custo")
    print(
        validacao.sensibilidade_custo(quadro, sinais, custos, config).to_string(index=False)
    )
    print("   Vantagem que morre com o dobro da taxa estava dentro do ruido.\n")

    print(f"3. Teste de permutacao ({args.repeticoes} embaralhamentos)")
    print(
        "   "
        + validacao.teste_permutacao(
            quadro, sinais, custos, config, repeticoes=args.repeticoes
        ).resumo()
    )

    armazenamento.fechar()
    return 0


# ---------------------------------------------------------------- walkforward


def comando_walkforward(args) -> int:
    provedor, armazenamento = _contexto(args)
    estrategia_base = construir(args.estrategia)
    quadro = _dados(args, estrategia_base, provedor, armazenamento)

    if args.estrategia == "rsi_macd":
        grade = grade_de_parametros(
            rsi_compra=[30.0, 35.0, 40.0], rsi_venda=[60.0, 65.0, 70.0]
        )
        fabrica = lambda p: EstrategiaRsiMacd(ParametrosRsiMacd(**p))
    elif args.estrategia == "ema":
        grade = grade_de_parametros(
            ema_rapida=[13, 21, 34], ema_lenta=[55, 89], adx_minimo=[15.0, 20.0, 25.0]
        )
        fabrica = lambda p: EstrategiaCruzamentoEma(ParametrosCruzamentoEma(**p))
    else:
        grade = [{}]
        fabrica = lambda p: construir(args.estrategia)

    relatorio = executar_walkforward(
        quadro,
        fabrica,
        grade,
        args.tf,
        _custos(args),
        _config(args),
        meses_treino=args.meses_treino,
        meses_teste=args.meses_teste,
    )

    print(f"=== walk-forward: {args.par} {args.tf} | {args.estrategia} ===")
    print(relatorio.resumo())
    print()

    if relatorio.metricas is None:
        print("Nenhum trade fora da amostra - nada a concluir.")
        armazenamento.fechar()
        return 0

    configuracao = _config(args)
    trades = relatorio.trades_fora_da_amostra
    print(
        met.formatar_relatorio(
            relatorio.metricas,
            {
                "custo_ida_e_volta": _custos(args).custo_ida_e_volta,
                "dimensionamento": configuracao.dimensionamento,
                "risco_por_trade": configuracao.risco_por_trade,
                "fracao_media": float(trades["fracao"].mean()) if len(trades) else 0.0,
                "pct_saidas_ambiguas": (
                    float(trades["ambiguo"].mean()) if len(trades) else 0.0
                ),
            },
            "FORA DA AMOSTRA (o unico numero que vale)",
        )
    )

    if relatorio.metricas_dentro_da_amostra:
        dentro = sum(m.expectancia_r for m in relatorio.metricas_dentro_da_amostra) / len(
            relatorio.metricas_dentro_da_amostra
        )
        print(
            f"\nExpectancia media dentro da amostra: {dentro:+.3f} R  "
            f"contra {relatorio.metricas.expectancia_r:+.3f} R fora."
        )
        print(
            "A diferenca entre os dois e quanto da estrategia era decoreba de ruido."
        )

    armazenamento.fechar()
    return 0


# ------------------------------------------------------------------- calibrar


def comando_calibrar(args) -> int:
    from nucleo.backtest import calibragem

    _, armazenamento = _contexto(args)
    trades = armazenamento.trades(args.filtro)
    if trades.empty:
        raise SystemExit(
            "Nenhum trade gravado. Rode 'backtest --salvar' antes para popular "
            "a tabela resultados_sinal."
        )

    quadro = calibragem.preparar(trades)
    print(f"=== calibragem de stop e alvo | {len(quadro)} trades ===\n")
    print(calibragem.resumo_excursao(quadro).to_string(index=False))
    print()
    print(calibragem.relatorio(quadro))

    print("\n\nEXPECTANCIA POR COMBINACAO (R por trade)")
    tabela = calibragem.tabela_calibragem(quadro)
    print(tabela.to_string(index=False))
    print(
        "\n  'extrapolado' e a fatia de trades cujo caminho nao chegamos a observar\n"
        "  ate esse nivel - um stop mais largo que o original nao pode ser avaliado\n"
        "  num trade que foi estopado. So as linhas com 'confiavel = sim' sustentam\n"
        "  conclusao; as outras sao chute com aparencia de conta."
    )

    confiaveis = tabela[tabela.confiavel == "sim"]
    if not confiaveis.empty:
        melhor = confiaveis.loc[confiaveis.expectancia_R.idxmax()]
        print(
            f"\n  Melhor combinacao confiavel: stop {melhor.stop_R}R / alvo "
            f"{melhor.alvo_R}R -> {melhor.expectancia_R:+.3f} R por trade."
        )
        print(
            "  Isto e uma hipotese, nao um resultado: reconfigure a estrategia com\n"
            "  esses niveis e rode 'backtest' e 'validar' de novo. Escolher a melhor\n"
            "  celula de uma tabela e depois reportar ela e otimizacao circular."
        )

    armazenamento.fechar()
    return 0


# ------------------------------------------------------------------ monitorar


def comando_monitorar(args) -> int:
    from nucleo import coletor

    provedor, armazenamento = _contexto(args)
    nomes = list(REGISTRO) if args.estrategia == "todas" else [
        n.strip() for n in args.estrategia.split(",")
    ]
    estrategias = [construir(n) for n in nomes]
    pares = [p.strip() for p in args.pares.split(",")]
    timeframes = [t.strip() for t in args.tfs.split(",")]

    print(f"=== coleta ao vivo | {len(estrategias)} setups ===")
    for e in estrategias:
        print(f"  - {e.nome}")
    print(f"pares      : {', '.join(pares)}")
    print(f"timeframes : {', '.join(timeframes)}")
    duracao = "sem prazo (ate ser interrompido)" if args.minutos <= 0 else f"{args.minutos} min"
    print(f"duracao    : {duracao}, consultando a cada {args.intervalo}s")
    print("NENHUMA ordem sera enviada. So leitura de mercado e gravacao.\n")

    def mostrar(registro: dict) -> None:
        agora = datetime.now(timezone.utc).strftime("%d/%m %H:%M:%S")
        if "erro" in registro:
            print(f"  {agora} ! {registro['erro']}", flush=True)
            return
        if "pulso" in registro:
            print(f"  {agora} . {registro['pulso']}", flush=True)
            return
        rotulo = {1: "COMPRA", -1: "VENDA", 0: "-"}[registro["direcao"]]
        marca = "  <<<" if registro["direcao"] != 0 else ""
        print(
            f"  {registro['vela']:%H:%M} {registro['par']:<10} "
            f"{registro['timeframe']:<4} {registro['fechamento']:>12,.4f}  "
            f"{rotulo:<7}{marca}  {registro.get('estrategia','')}"
        )

    # Servico precisa parar com calma: `docker stop` e `systemctl stop` mandam
    # SIGTERM e esperam poucos segundos antes de matar. Como cada vela ja foi
    # gravada quando fechou, parar aqui nao perde dado - so evita deixar a
    # conexao do banco aberta.
    pedido_de_parada = {"sim": False}

    def encerrar(numero, _quadro):
        pedido_de_parada["sim"] = True
        print(f"  sinal {numero} recebido, encerrando a coleta...", flush=True)

    for evento in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(evento, encerrar)
        except (ValueError, OSError):
            pass

    ao_observar = None
    escritor = None
    if args.jsonl:
        from nucleo import exportacao

        escritor = exportacao.EscritorJsonl(args.jsonl)
        ao_observar = escritor
        print(
            f"gravando tambem em {escritor.caminho} "
            f"({len(escritor.vistas)} observacoes ja no arquivo)"
        )

    resumo = coletor.coletar(
        pares, timeframes, estrategias, provedor, armazenamento,
        minutos=args.minutos, intervalo_segundos=args.intervalo, ao_registrar=mostrar,
        parar=lambda: pedido_de_parada["sim"],
        ciclos_maximos=args.ciclos if args.ciclos > 0 else None,
        velas_por_ciclo=args.velas,
        ao_observar=ao_observar,
    )

    print(f"\n--- coleta encerrada ---")
    if escritor is not None:
        print(
            f"jsonl: {escritor.gravadas} gravadas, "
            f"{escritor.repetidas} ja existiam e foram ignoradas"
        )
    print(f"ciclos {resumo.ciclos} | velas novas {resumo.velas_novas} | erros {resumo.erros}")
    if resumo.por_alvo:
        print("\nvelas por alvo:")
        for (par, timeframe), quantidade in sorted(resumo.por_alvo.items()):
            print(f"  {par:<10} {timeframe:<4} {quantidade:>4}")
    print(f"\nsinais emitidos: {len(resumo.sinais)}")
    for sinal in resumo.sinais:
        print(f"  {sinal['vela']:%H:%M} {sinal['par']:<10} {sinal['timeframe']:<4} "
              f"{'COMPRA' if sinal['direcao'] > 0 else 'VENDA':<7} "
              f"({sinal['forca']:.0%})  {sinal['estrategia']}")

    print("\n--- paridade ao vivo x backtest ---")
    paridade = coletor.conferir_paridade(
        pares, timeframes, estrategias, provedor, armazenamento
    )
    if paridade.empty:
        print("  nada a conferir ainda.")
    else:
        print(paridade.to_string(index=False))
        if (paridade.divergencias == 0).all():
            print(
                "\n  Os dois caminhos concordam em todas as velas: decidir ao vivo\n"
                "  e decidir no backtest produzem o mesmo sinal."
            )
        else:
            print(
                "\n  !! DIVERGENCIA. O caminho ao vivo e o simulado nao sao o mesmo\n"
                "  sistema - qualquer metrica de backtest deixa de valer ate isso ser\n"
                "  explicado."
            )

    armazenamento.fechar()

    # O codigo de saida precisa dizer a verdade. Um ciclo em que TODOS os
    # alvos falharam e zero vela entrou nao e sucesso - mas sem isso ele
    # sai 0, o GitHub pinta de verde e a coleta morre em silencio por dias
    # sem ninguem notar. Foi o que aconteceu na execucao agendada que tomou
    # 451 da Binance nos quinze alvos e reportou sucesso.
    if resumo.erros and not resumo.velas_novas:
        print(
            f"\nFALHA: {resumo.erros} erro(s) e nenhuma vela coletada. "
            f"A execucao nao trouxe dado nenhum."
        )
        return 1
    if resumo.erros:
        print(
            f"\nAviso: {resumo.erros} alvo(s) falharam, mas "
            f"{resumo.velas_novas} vela(s) foram coletadas."
        )
    return 0


# ------------------------------------------------------------------- importar


def comando_importar(args) -> int:
    """Leva as observacoes coletadas na nuvem para o banco local."""
    from glob import glob

    from nucleo import exportacao

    _, armazenamento = _contexto(args)
    caminhos = sorted(glob(args.padrao))
    if not caminhos:
        raise SystemExit(f"Nenhum arquivo casou com {args.padrao!r}.")

    print(f"lendo {len(caminhos)} arquivo(s):")
    for caminho in caminhos:
        print(f"  {caminho}")

    gravadas = exportacao.importar(armazenamento, caminhos)
    total = len(armazenamento.observacoes())
    print(f"novas observacoes: {gravadas} | total no banco: {total}")
    armazenamento.fechar()
    return 0


# ------------------------------------------------------------------- rastrear


def comando_rastrear(args) -> int:
    """Acompanha cada sinal coletado ate o desfecho e mede o acerto real."""
    from glob import glob

    import pandas as pd

    from nucleo import exportacao, rastreio

    provedor, armazenamento = _contexto(args)

    if args.padrao:
        caminhos = sorted(glob(args.padrao))
        if not caminhos:
            raise SystemExit(f"Nenhum arquivo casou com {args.padrao!r}.")
        print(f"lendo {len(caminhos)} arquivo(s) de observacoes")
        observacoes = exportacao.ler(caminhos)
    else:
        print("lendo observacoes do banco")
        observacoes = armazenamento.observacoes()
        if not observacoes.empty:
            observacoes["vela"] = pd.to_datetime(
                observacoes["vela_ms"], unit="ms", utc=True
            )

    if observacoes.empty:
        raise SystemExit(
            "Nenhuma observacao encontrada. Rode 'monitorar' ou 'importar' antes."
        )

    sinais = rastreio.preparar_observacoes(observacoes)
    print(
        f"{len(observacoes)} observacoes | {len(sinais)} sao sinal com stop e alvo"
    )
    if sinais.empty:
        raise SystemExit(
            "Nenhum sinal aproveitavel ainda - so velas sem recomendacao, ou "
            "sinais gravados antes de o stop e o alvo passarem a ser salvos."
        )

    print("reconstruindo os desfechos pelo motor do backtest...")
    trades = rastreio.rastrear(
        sinais, provedor, armazenamento, _custos(args), _config(args),
        usar_rede=not args.offline,
    )

    print()
    print("=== ASSERTIVIDADE POR SETUP (dados coletados ao vivo) ===")
    resumo = rastreio.resumir(trades, por="estrategia")
    print(resumo.to_string(index=False) if not resumo.empty else "  nada fechado ainda")

    if args.detalhe and not trades.empty:
        print()
        print("=== POR PAR E TIMEFRAME ===")
        trades["alvo_detalhe"] = (
            trades["par"] + " " + trades["timeframe"]
        )
        print(rastreio.resumir(trades, por="alvo_detalhe").to_string(index=False))

    print()
    print(rastreio.texto_do_veredito(trades))

    if args.salvar and not trades.empty:
        destino = args.salvar
        trades.to_csv(destino, index=False)
        print(f"\ntrades gravados em {destino}")

    armazenamento.fechar()
    return 0



# -------------------------------------------------------------------- simular


def comando_simular(args) -> int:
    """Traduz os setups para dinheiro: quanto uma banca vira em N dias."""
    import numpy as np

    from nucleo.backtest.simulacao import ConfigBanca, comparar

    provedor, armazenamento = _contexto(args)
    pares = [p.strip() for p in args.pares.split(",")]
    inicio = datetime.now(timezone.utc) - timedelta(days=args.dias)
    aquecimento = max(construir(n).barras_de_aquecimento() for n in ESTRATEGIAS)

    print(f"carregando {args.dias} dias de {args.tf} para {len(pares)} pares...")
    quadros = {}
    for par in pares:
        quadros[par] = carregar(
            par, args.tf, inicio, provedor=provedor, armazenamento=armazenamento,
            barras_aquecimento=aquecimento, usar_rede=not args.offline,
        )

    por_setup = {}
    for nome in ESTRATEGIAS:
        todos = []
        for par, quadro in quadros.items():
            if quadro.empty:
                continue
            resultado = executar(
                quadro, construir(nome).gerar_sinais(quadro), _custos(args), _config(args)
            )
            trades = resultado.trades.copy()
            trades["par"] = par
            todos.append(trades)
        por_setup[nome] = (
            pd.concat(todos, ignore_index=True) if todos else pd.DataFrame()
        )

    banca = ConfigBanca(
        inicial=args.banca,
        risco_por_trade=args.risco,
        valor_minimo_ordem=args.ordem_minima,
        moeda=args.moeda,
    )

    print()
    print(
        f"=== {args.moeda} {args.banca:g} em {args.dias} dias | {args.tf} | "
        f"{len(pares)} pares | risco {args.risco:.0%} por trade ==="
    )
    print(comparar(por_setup, banca).to_string(index=False))

    retornos = [
        float(q.fechamento.iloc[-1] / q.abertura.iloc[0] - 1)
        for q in quadros.values() if len(q) > 1
    ]
    if retornos:
        print()
        print(f"comprar e segurar no mesmo periodo: media {np.mean(retornos):+.1%}")

    print()
    print(
        "Cuidado ao ler: este periodo ja foi olhado, entao o numero mede a\n"
        "MECANICA (dimensionamento, custo, frequencia), nao a vantagem. Para\n"
        "vantagem, so o que a coleta ao vivo acumular daqui para frente."
    )
    armazenamento.fechar()
    return 0



# ------------------------------------------------------------------- conjunto


def _pares(args) -> list[str]:
    return [p.strip() for p in args.pares.split(",") if p.strip()]


def _nomes(args) -> list[str]:
    return list(REGISTRO) if args.estrategias == "todas" else [
        n.strip() for n in args.estrategias.split(",") if n.strip()
    ]


def _quadros(args, provedor, armazenamento, nomes) -> dict:
    inicio = (
        datetime.fromisoformat(args.desde).replace(tzinfo=timezone.utc)
        if getattr(args, "desde", None)
        else datetime.now(timezone.utc) - timedelta(days=args.dias)
    )
    aquecimento = max(construir(n).barras_de_aquecimento() for n in nomes)
    return {
        par: carregar(
            par, args.tf, inicio, provedor=provedor, armazenamento=armazenamento,
            barras_aquecimento=aquecimento, usar_rede=not args.offline,
        )
        for par in _pares(args)
    }


def comando_conjunto(args) -> int:
    """Monta o conjunto de treino de um setup a partir do historico."""
    from nucleo.aprendizado import conjunto as cj

    provedor, armazenamento = _contexto(args)
    quadros = _quadros(args, provedor, armazenamento, [args.estrategia])

    total = cj.vazio()
    for par, quadro in quadros.items():
        if quadro.empty:
            continue
        estrategia = construir(args.estrategia)
        resultado = executar(quadro, estrategia.gerar_sinais(quadro), _custos(args), _config(args))
        parte = cj.montar(quadro, estrategia, resultado.trades, par=par, timeframe=args.tf)
        total = total.concatenar(parte)
        print(f"  {par:<10} {len(parte):>5} exemplos")

    total = total.ordenar_por_tempo()
    if total.vazio:
        raise SystemExit("Nenhum trade fechado: nao ha o que aprender.")

    destino = args.saida or f"dados/conjuntos/{args.estrategia}_{args.tf}.csv"
    cj.salvar_csv(total, destino)
    print()
    print(f"{len(total)} exemplos | {total.entradas.shape[1]} colunas | acerto base "
          f"{total.rotulos.venceu.mean():.1%}")
    print(f"gravado em {destino}")
    armazenamento.fechar()
    return 0


# --------------------------------------------------------------------- filtro


def comando_filtro(args) -> int:
    """Avalia o filtro de ML por walk-forward com controle embaralhado."""
    from nucleo.aprendizado import conjunto as cj
    from nucleo.aprendizado.filtro import ConfigFiltro, FiltroML, avaliar_walkforward

    conjunto = cj.ler_csv(args.conjunto)
    if conjunto.vazio:
        raise SystemExit(f"Conjunto vazio: {args.conjunto}")
    config = ConfigFiltro(limiar=args.limiar)

    print(f"=== filtro | {len(conjunto)} exemplos | acerto base "
          f"{conjunto.rotulos.venceu.mean():.1%} | limiar {args.limiar:.0%} ===")
    print()
    relatorio = avaliar_walkforward(
        conjunto, config, meses_teste=args.meses_teste, minimo_treino=args.minimo_treino
    )
    if relatorio.janelas:
        tabela = pd.DataFrame(
            [
                {
                    "corte": j.corte.date(), "treino": j.n_treino, "teste": j.n_teste,
                    "mantidos": j.mantidos, "auc": round(j.auc, 3),
                    "R_antes": round(j.r_total_antes, 1), "R_depois": round(j.r_total_depois, 1),
                    "acerto_antes": f"{j.acerto_antes:.0%}", "acerto_depois": f"{j.acerto_depois:.0%}",
                }
                for j in relatorio.janelas
            ]
        )
        print(tabela.to_string(index=False))
        print()
    print(relatorio.veredito())

    if args.salvar:
        filtro = FiltroML(config).treinar(conjunto)
        filtro.salvar(args.salvar)
        print()
        print(f"modelo final treinado em TODO o conjunto e gravado em {args.salvar}")
        print("(o veredito acima e que diz se ele merece ser usado - o arquivo nao)")
        print()
        print("colunas que mais pesam:")
        print(filtro.importancias(conjunto).head(8).round(4).to_string())
    return 0


# ------------------------------------------------------------------- carteira


def comando_carteira(args) -> int:
    """Passa os setups pela mesma banca, com posicoes simultaneas e regras."""
    from nucleo.risco.carteira import RegrasCarteira, relatorio, simular_carteira

    provedor, armazenamento = _contexto(args)
    nomes = _nomes(args)
    quadros = _quadros(args, provedor, armazenamento, nomes)

    todos = []
    for nome in nomes:
        for par, quadro in quadros.items():
            if quadro.empty:
                continue
            resultado = executar(quadro, construir(nome).gerar_sinais(quadro), _custos(args), _config(args))
            trades = resultado.trades.copy()
            trades["par"] = par
            trades["estrategia"] = nome
            todos.append(trades)
    if not todos:
        raise SystemExit("Nenhum trade gerado.")

    regras = RegrasCarteira(
        saldo_inicial=args.banca, risco_por_trade=args.risco, max_posicoes=args.max_posicoes,
        max_por_par=args.max_por_par, exposicao_maxima=args.exposicao,
        perda_diaria_maxima=args.perda_diaria, perdas_seguidas_para_pausa=args.perdas_para_pausa,
        sinais_de_pausa=args.sinais_de_pausa, valor_minimo_ordem=args.ordem_minima, moeda=args.moeda,
    )
    resultado = simular_carteira(pd.concat(todos, ignore_index=True), regras)
    periodo = f"desde {args.desde}" if args.desde else f"{args.dias} dias"
    print(f"=== carteira | {', '.join(nomes)} | {args.tf} | {len(quadros)} pares | {periodo} ===")
    print(relatorio(resultado))
    print()
    print("Cuidado ao ler: periodo ja olhado; isto mede a MECANICA da gestao de banca,")
    print("nao a vantagem dos setups. Vantagem so a coleta ao vivo confirma.")
    armazenamento.fechar()
    return 0


# -------------------------------------------------------------------- decidir


def comando_decidir(args) -> int:
    """Varre o mercado e diz o que faria agora, com quanto, e por que nao."""
    import json as _json
    from pathlib import Path

    from nucleo import decisao
    from nucleo.aprendizado.filtro import FiltroML
    from nucleo.risco.carteira import Carteira, RegrasCarteira

    provedor, armazenamento = _contexto(args)
    nomes = _nomes(args)
    estrategias = [construir(n) for n in nomes]
    timeframes = [t.strip() for t in args.tfs.split(",") if t.strip()]

    filtros = {}
    if args.filtros:
        for estrategia in estrategias:
            caminho = Path(args.filtros) / f"{estrategia.nome}.pkl"
            if caminho.exists():
                filtros[estrategia.nome] = FiltroML.carregar(str(caminho))
        print(f"filtros de ML carregados: {len(filtros)}")

    carteira = Carteira(RegrasCarteira(
        saldo_inicial=args.banca, risco_por_trade=args.risco, max_posicoes=args.max_posicoes,
        max_por_par=args.max_por_par, exposicao_maxima=args.exposicao,
        valor_minimo_ordem=args.ordem_minima, moeda=args.moeda,
    ))

    recomendacoes = decisao.varrer(
        _pares(args), timeframes, estrategias, provedor, armazenamento, carteira,
        filtros=filtros, usar_rede=not args.offline,
    )

    print(f"=== decisao | {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC | banca "
          f"{args.moeda} {args.banca:g} | {len(nomes)} setups | {', '.join(timeframes)} ===")
    if not recomendacoes:
        print("Nenhum sinal ativo na ultima vela fechada de nenhum par/timeframe.")
    else:
        pd.set_option("display.width", 220)
        print(decisao.tabela(recomendacoes).to_string(index=False))
        entrar = [r for r in recomendacoes if r.decisao == decisao.ENTRAR]
        print()
        print(f"{len(entrar)} entrada(s) recomendada(s), {len(recomendacoes) - len(entrar)} recusada(s).")

    if args.json:
        Path(args.json).write_text(
            _json.dumps(decisao.para_json(recomendacoes), ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        print(f"gravado em {args.json}")

    print()
    print("Recomendacao e registro do que o sistema faria. Nenhuma ordem foi enviada.")
    armazenamento.fechar()
    return 0



# ----------------------------------------------------------------------- main


def montar_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cli.py", description="Analise tecnica e backtest de cripto."
    )
    sub = parser.add_subparsers(dest="comando", required=True)

    def comuns(p, com_estrategia=True):
        p.add_argument("--par", default="BTC/USDT")
        p.add_argument("--tf", default="1h", help="1m 5m 15m 30m 1h 4h 1d")
        p.add_argument("--corretora", default=None, help="padrao: EXCHANGE ou binance")
        p.add_argument("--banco", default=None, help="padrao: BANCO_DADOS ou dados_mercado.db")
        p.add_argument("--desde", default=None, help="AAAA-MM-DD")
        p.add_argument("--ate", default=None, help="AAAA-MM-DD")
        p.add_argument("--dias", type=int, default=365)
        if com_estrategia:
            p.add_argument("--estrategia", default="rsi_macd", choices=ESTRATEGIAS)
            p.add_argument("--offline", action="store_true", help="so cache, sem rede")

    def de_execucao(p):
        p.add_argument("--taxa", type=float, default=0.001, help="por lado; 0.001 = 0,1%%")
        p.add_argument("--slippage", type=float, default=0.0005, help="por lado")
        p.add_argument("--max-barras", type=int, default=48, dest="max_barras")
        p.add_argument(
            "--ambiguidade", default="pessimista", choices=("pessimista", "otimista")
        )
        p.add_argument(
            "--dimensionamento", default="risco", choices=("risco", "fixo"),
            help="risco: posicao calculada pela distancia do stop; fixo: sempre 100%%",
        )
        p.add_argument(
            "--risco", type=float, default=0.02,
            help="fracao do capital arriscada por trade no modo risco (0.02 = 2%%)",
        )

    p = sub.add_parser("baixar", help="baixa velas para o cache local")
    comuns(p, com_estrategia=False)
    p.set_defaults(funcao=comando_baixar)

    p = sub.add_parser("analisar", help="sinal na ultima vela fechada")
    comuns(p)
    p.set_defaults(funcao=comando_analisar)

    p = sub.add_parser("backtest", help="mede a estrategia no historico")
    comuns(p)
    de_execucao(p)
    p.add_argument("--salvar", action="store_true", help="grava trades no banco")
    p.set_defaults(funcao=comando_backtest)

    p = sub.add_parser("comparar", help="ranqueia estrategias lado a lado")
    comuns(p)
    de_execucao(p)
    p.add_argument("--estrategias", default=None, help="lista separada por virgula")
    p.add_argument("--correlacao", action="store_true")
    p.set_defaults(funcao=comando_comparar)

    p = sub.add_parser("validar", help="permutacao e sensibilidade a atraso e custo")
    comuns(p)
    de_execucao(p)
    p.add_argument("--repeticoes", type=int, default=200)
    p.set_defaults(funcao=comando_validar)

    p = sub.add_parser("calibrar", help="usa MFE/MAE dos trades para calibrar stop e alvo")
    p.add_argument("--banco", default=None)
    p.add_argument("--corretora", default=None)
    p.add_argument("--filtro", default=None, help="restringe a uma estrategia")
    p.set_defaults(funcao=comando_calibrar)

    p = sub.add_parser("monitorar", help="coleta ao vivo com dados reais, sem operar")
    comuns(p, com_estrategia=False)
    p.add_argument(
        "--estrategia", default="todas",
        help="'todas', ou nomes separados por virgula: " + ", ".join(ESTRATEGIAS),
    )
    p.add_argument("--pares", default="BTC/USDT,ETH/USDT,SOL/USDT")
    p.add_argument("--tfs", default="1m,5m,15m", help="timeframes, separados por virgula")
    p.add_argument(
        "--minutos", type=int, default=60,
        help="0 = roda indefinidamente, ate Ctrl+C ou parada do servico",
    )
    p.add_argument("--intervalo", type=int, default=20, help="segundos entre consultas")
    p.add_argument(
        "--ciclos", type=int, default=0,
        help="para depois de N passagens; 0 = sem limite. Use 1 no GitHub Actions",
    )
    p.add_argument(
        "--jsonl", default=None, metavar="PASTA",
        help="grava tambem em JSONL nesta pasta, um arquivo por mes",
    )
    p.add_argument(
        "--velas", type=int, default=1, metavar="N",
        help="grava as N ultimas velas fechadas por ciclo, nao so a ultima. "
             "Use um valor que cubra o intervalo entre execucoes",
    )
    p.set_defaults(funcao=comando_monitorar)

    def de_banca(p):
        p.add_argument("--banca", type=float, default=100.0)
        p.add_argument("--moeda", default="USDT")
        p.add_argument("--risco", type=float, default=0.02)
        p.add_argument("--max-posicoes", type=int, default=3, dest="max_posicoes")
        p.add_argument("--max-por-par", type=int, default=1, dest="max_por_par")
        p.add_argument("--exposicao", type=float, default=1.0)
        p.add_argument("--ordem-minima", type=float, default=1.0, dest="ordem_minima")

    def de_mercado(p):
        p.add_argument("--banco", default=None)
        p.add_argument("--corretora", default=None)
        p.add_argument("--pares", default="BTC/USDT,ETH/USDT,SOL/USDT,BNB/USDT,XRP/USDT")
        p.add_argument("--tf", default="4h")
        p.add_argument("--dias", type=int, default=365)
        p.add_argument("--desde", default=None, help="AAAA-MM-DD")
        p.add_argument("--offline", action="store_true")
        p.add_argument("--taxa", type=float, default=0.001)
        p.add_argument("--slippage", type=float, default=0.0005)
        p.add_argument("--max-barras", type=int, default=48, dest="max_barras")
        p.add_argument("--ambiguidade", default="pessimista", choices=("pessimista", "otimista"))
        p.add_argument("--dimensionamento", default="risco", choices=("risco", "fixo"))

    p = sub.add_parser("conjunto", help="monta o conjunto de treino de um setup")
    de_mercado(p)
    p.add_argument("--estrategia", default="ema", choices=ESTRATEGIAS)
    p.add_argument("--risco", type=float, default=0.02)
    p.add_argument("--saida", default=None, help="CSV de saida (padrao: dados/conjuntos/<setup>_<tf>.csv)")
    p.set_defaults(funcao=comando_conjunto)

    p = sub.add_parser("filtro", help="avalia o filtro de ML por walk-forward")
    p.add_argument("--conjunto", required=True, help="CSV gerado por 'conjunto'")
    p.add_argument("--limiar", type=float, default=0.5)
    p.add_argument("--meses-teste", type=int, default=6, dest="meses_teste")
    p.add_argument("--minimo-treino", type=int, default=100, dest="minimo_treino")
    p.add_argument("--salvar", default=None, metavar="PKL", help="treina no conjunto todo e grava")
    p.set_defaults(funcao=comando_filtro)

    p = sub.add_parser("carteira", help="banca compartilhada com regras e posicoes simultaneas")
    de_mercado(p)
    de_banca(p)
    p.add_argument("--estrategias", default="confluencia,rsi_macd,compressao")
    p.add_argument("--perda-diaria", type=float, default=0.06, dest="perda_diaria")
    p.add_argument("--perdas-para-pausa", type=int, default=4, dest="perdas_para_pausa")
    p.add_argument("--sinais-de-pausa", type=int, default=3, dest="sinais_de_pausa")
    p.set_defaults(funcao=comando_carteira)

    p = sub.add_parser("decidir", help="o que o sistema faria agora, com quanto, e por que nao")
    p.add_argument("--banco", default=None)
    p.add_argument("--corretora", default=None)
    p.add_argument("--pares", default="BTC/USDT,ETH/USDT,SOL/USDT,BNB/USDT,XRP/USDT")
    p.add_argument("--tfs", default="4h")
    p.add_argument("--estrategias", default="confluencia,rsi_macd,compressao")
    p.add_argument("--filtros", default=None, metavar="PASTA", help="pasta com <setup>.pkl")
    p.add_argument("--json", default=None, metavar="ARQUIVO")
    p.add_argument("--offline", action="store_true")
    de_banca(p)
    p.set_defaults(funcao=comando_decidir)

    p = sub.add_parser("simular", help="quanto uma banca vira em cada setup")
    p.add_argument("--banco", default=None)
    p.add_argument("--corretora", default=None)
    p.add_argument("--pares", default="BTC/USDT,ETH/USDT,SOL/USDT,BNB/USDT,XRP/USDT")
    p.add_argument("--tf", default="4h")
    p.add_argument("--dias", type=int, default=90)
    p.add_argument("--banca", type=float, default=100.0)
    p.add_argument("--moeda", default="USDT")
    p.add_argument("--ordem-minima", type=float, default=1.0, dest="ordem_minima")
    p.add_argument("--offline", action="store_true")
    p.add_argument("--taxa", type=float, default=0.001)
    p.add_argument("--slippage", type=float, default=0.0005)
    p.add_argument("--max-barras", type=int, default=48, dest="max_barras")
    p.add_argument("--ambiguidade", default="pessimista", choices=("pessimista", "otimista"))
    p.add_argument("--dimensionamento", default="risco", choices=("risco", "fixo"))
    p.add_argument("--risco", type=float, default=0.02)
    p.set_defaults(funcao=comando_simular)

    p = sub.add_parser("rastrear", help="mede o acerto real dos sinais coletados")
    p.add_argument("--banco", default=None)
    p.add_argument("--corretora", default=None)
    p.add_argument(
        "--padrao", default=None,
        help="arquivos JSONL; sem isso, le as observacoes do banco",
    )
    p.add_argument("--offline", action="store_true", help="so cache, sem rede")
    p.add_argument("--detalhe", action="store_true", help="abre por par e timeframe")
    p.add_argument("--salvar", default=None, metavar="CSV", help="grava os trades")
    p.add_argument("--taxa", type=float, default=0.001)
    p.add_argument("--slippage", type=float, default=0.0005)
    p.add_argument("--max-barras", type=int, default=48, dest="max_barras")
    p.add_argument("--ambiguidade", default="pessimista", choices=("pessimista", "otimista"))
    p.add_argument("--dimensionamento", default="risco", choices=("risco", "fixo"))
    p.add_argument("--risco", type=float, default=0.02)
    p.set_defaults(funcao=comando_rastrear)

    p = sub.add_parser("importar", help="carrega observacoes JSONL no banco local")
    p.add_argument("--banco", default=None)
    p.add_argument("--corretora", default=None)
    p.add_argument(
        "--padrao", default="dados/observacoes/*.jsonl",
        help="caminho ou padrao dos arquivos JSONL",
    )
    p.set_defaults(funcao=comando_importar)

    p = sub.add_parser("walkforward", help="otimiza no treino, mede no que veio depois")
    comuns(p)
    de_execucao(p)
    p.add_argument("--meses-treino", type=int, default=12, dest="meses_treino")
    p.add_argument("--meses-teste", type=int, default=3, dest="meses_teste")
    p.set_defaults(funcao=comando_walkforward)

    return parser


def principal(argumentos: list[str] | None = None) -> int:
    args = montar_parser().parse_args(argumentos)
    return args.funcao(args)


if __name__ == "__main__":
    sys.exit(principal())
