import { useEffect, useMemo, useRef, useState } from "react";
import {
  CandlestickSeries,
  ColorType,
  LineSeries,
  LineStyle,
  createChart,
  createSeriesMarkers,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import { api, type Estado, type Operacao } from "../api";
import { usarDados } from "../usarDados";
import { dataHora, lado, numero, pct, preco } from "../formato";
import { Botao, Campo, Carregando, Delta, Erro, Etiqueta, Painel, Par, Tabela, Vazio, td, tdNum, th, thNum, type Tom } from "../componentes";

const coresLinhas = ["#4d8dff", "#f5b83d", "#22c1c3", "#c084fc", "#f472b6", "#a3e635", "#94a3b8", "#fb923c"];
// Uma cor por setup no modo "todos". Verde e vermelho ficam reservados para
// resultado, entao nenhum setup usa os dois.
const coresSetups = ["#4d8dff", "#f5b83d", "#22c1c3", "#c084fc", "#f472b6", "#a3e635", "#fb923c", "#94a3b8", "#e879f9"];

const desfechos: Record<string, { rotulo: string; tom: Tom }> = {
  ALVO: { rotulo: "bateu o alvo", tom: "alta" },
  STOP: { rotulo: "bateu o stop", tom: "baixa" },
  TEMPO: { rotulo: "saiu por tempo", tom: "atencao" },
  FIM_DADOS: { rotulo: "em aberto", tom: "neutro" },
};

function desfecho(op: Operacao) {
  return desfechos[op.motivo_saida] ?? { rotulo: op.motivo_saida.toLowerCase(), tom: "neutro" as Tom };
}

function Ponto({ cor }: { cor: string }) {
  return <span className="inline-block h-2 w-2 rounded-full" style={{ background: cor }} />;
}

export function Grafico({ estado, parametros }: { estado: Estado | null; parametros: URLSearchParams }) {
  const pares = estado?.pares ?? [];
  const tfs = estado?.timeframes ?? [];
  const setups = estado?.setups ?? [];

  const [par, setPar] = useState(parametros.get("par") ?? pares[0] ?? "BTC/USDT");
  const [tf, setTf] = useState(parametros.get("tf") ?? "4h");
  const [setup, setSetup] = useState(parametros.get("setup") ?? "todos");
  const [barras, setBarras] = useState(300);

  useEffect(() => {
    const p = parametros.get("par");
    const t = parametros.get("tf");
    const s = parametros.get("setup");
    if (p) setPar(p);
    if (t) setTf(t);
    if (s) setSetup(s);
  }, [parametros]);

  const { dados, erro, carregando, recarregar } = usarDados(() => api.velas(par, tf, setup, barras), `velas:${par}:${tf}:${setup}:${barras}`);
  const ref = useRef<HTMLDivElement>(null);
  const todos = dados?.por_setup ?? null;

  const legendaLinhas = useMemo(() => (dados ? Object.keys(dados.linhas).map((nome, i) => ({ nome, cor: coresLinhas[i % coresLinhas.length] })) : []), [dados]);
  const corDoSetup = (nome: string) => {
    const i = todos ? todos.findIndex((s) => s.nome === nome) : -1;
    return coresSetups[(i < 0 ? 0 : i) % coresSetups.length];
  };

  useEffect(() => {
    const el = ref.current;
    if (!el || !dados) return;
    const chart = createChart(el, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: "#0b0f15" },
        textColor: "#8a95a6",
        fontFamily: "JetBrains Mono, ui-monospace, monospace",
        fontSize: 11,
      },
      grid: { vertLines: { color: "#131a24" }, horzLines: { color: "#131a24" } },
      rightPriceScale: { borderColor: "#1b2330" },
      timeScale: { borderColor: "#1b2330", timeVisible: true, secondsVisible: false, rightOffset: 4 },
      crosshair: {
        horzLine: { color: "#29343f", labelBackgroundColor: "#29343f" },
        vertLine: { color: "#29343f", labelBackgroundColor: "#29343f" },
      },
      localization: { locale: "pt-BR" },
    });
    const velas = chart.addSeries(CandlestickSeries, {
      upColor: "#19c37d",
      downColor: "#f0435a",
      wickUpColor: "#19c37d",
      wickDownColor: "#f0435a",
      borderVisible: false,
    });
    velas.setData(dados.velas.map((v) => ({ time: v.t as UTCTimestamp, open: v.o, high: v.h, low: v.l, close: v.c })));

    const marcas: SeriesMarker<Time>[] = [];

    if (dados.por_setup) {
      // Modo "todos": cada setup com a propria cor; circulo onde entrou,
      // quadrado onde saiu com + ou - conforme o resultado.
      dados.por_setup.forEach((s, i) => {
        const cor = coresSetups[i % coresSetups.length];
        for (const op of s.operacoes) {
          marcas.push({ time: op.t_entrada as UTCTimestamp, position: "inBar", color: cor, shape: "circle", size: 1 });
          if (!op.aberto) {
            marcas.push({
              time: op.t_saida as UTCTimestamp,
              position: "inBar",
              color: cor,
              shape: "square",
              text: op.retorno_liquido_pct > 0 ? "+" : "−",
              size: 1,
            });
          }
        }
      });
    } else {
      Object.values(dados.linhas).forEach((pontos, i) => {
        const linha = chart.addSeries(LineSeries, {
          color: coresLinhas[i % coresLinhas.length],
          lineWidth: 1,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
          // Sem titulo na serie: o rotulo iria para o eixo de preco e brigaria
          // com stop/alvo. A legenda no cabecalho do painel ja nomeia as linhas.
        });
        linha.setData(pontos.map((p) => ({ time: p.t as UTCTimestamp, value: p.v })));
      });

      // Setas: onde o setup falou. Sem texto, para nao poluir.
      for (const s of dados.sinais) {
        marcas.push({
          time: s.t as UTCTimestamp,
          position: s.direcao > 0 ? "belowBar" : "aboveBar",
          color: s.direcao > 0 ? "#19c37d" : "#f0435a",
          shape: s.direcao > 0 ? "arrowUp" : "arrowDown",
        });
      }

      // Ultima operacao: circulo na vela em que o dinheiro entrou (abertura da
      // vela seguinte ao sinal) e quadrado na vela em que saiu.
      const op = dados.ultimo_trade;
      if (op) {
        marcas.push({ time: op.t_entrada as UTCTimestamp, position: "inBar", color: "#4d8dff", shape: "circle", text: "entrada", size: 1 });
        if (!op.aberto) {
          const d = desfecho(op);
          marcas.push({
            time: op.t_saida as UTCTimestamp,
            position: "inBar",
            color: d.tom === "alta" ? "#19c37d" : d.tom === "baixa" ? "#f0435a" : "#f5b83d",
            shape: "square",
            text: "saída",
            size: 1,
          });
        }
      }

      const u = dados.ultimo_sinal;
      const stop = op?.stop ?? u?.stop ?? null;
      const alvo = op?.alvo ?? u?.alvo ?? null;
      if (stop && alvo) {
        velas.createPriceLine({ price: stop, color: "#f0435a", lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: "stop" });
        velas.createPriceLine({ price: alvo, color: "#19c37d", lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: "alvo" });
      }
      if (op) {
        velas.createPriceLine({ price: op.preco_entrada, color: "#4d8dff", lineWidth: 1, lineStyle: LineStyle.Solid, axisLabelVisible: true, title: "entrada" });
      }
    }

    marcas.sort((a, b) => Number(a.time) - Number(b.time));
    createSeriesMarkers(velas, marcas);

    // O container pode ainda estar sem largura na primeira medida; sem isso as
    // velas ficam espremidas num canto quando o layout termina de assentar.
    const ajustar = () => chart.timeScale().fitContent();
    ajustar();
    const observador = new ResizeObserver(ajustar);
    observador.observe(el);
    return () => {
      observador.disconnect();
      chart.remove();
    };
  }, [dados]);

  const u = dados?.ultimo_sinal ?? null;
  const op = dados?.ultimo_trade ?? null;
  const risco = u?.stop ? Math.abs(u.preco - u.stop) : 0;
  const razao = u?.alvo && risco ? Math.abs(u.alvo - u.preco) / risco : null;

  const combinadas = useMemo(() => {
    const lista = todos ? todos.flatMap((s) => s.operacoes) : (dados?.operacoes ?? []);
    return [...lista].sort((a, b) => new Date(b.entrada).getTime() - new Date(a.entrada).getTime());
  }, [todos, dados]);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex flex-wrap gap-1 rounded-term border border-borda bg-painel p-1">
          {pares.map((p) => (
            <Botao key={p} pequeno ativo={p === par} onClick={() => setPar(p)}>
              {p.replace("/USDT", "")}
            </Botao>
          ))}
        </div>
        <div className="flex gap-1 rounded-term border border-borda bg-painel p-1">
          {tfs.map((t) => (
            <Botao key={t} pequeno ativo={t === tf} onClick={() => setTf(t)}>
              {t}
            </Botao>
          ))}
        </div>
        <select
          value={setup}
          onChange={(ev) => setSetup(ev.target.value)}
          className="mono h-8 rounded-term border border-borda bg-painel px-2 text-[11.5px] text-texto outline-none focus:border-acento"
        >
          <option value="todos">todos — entradas e saídas de todos os setups</option>
          {setups.map((s) => (
            <option key={s.nome} value={s.nome}>
              {s.nome} — {s.descricao}
            </option>
          ))}
        </select>
        <div className="flex gap-1 rounded-term border border-borda bg-painel p-1">
          {[150, 300, 600].map((n) => (
            <Botao key={n} pequeno ativo={n === barras} onClick={() => setBarras(n)}>
              {n}
            </Botao>
          ))}
        </div>
        <span className="eyebrow">velas</span>
      </div>

      {erro && <Erro erro={erro} tentar={() => recarregar(false)} />}

      <div className="grid gap-4 xl:grid-cols-[1fr_320px]">
        <Painel
          titulo={
            dados ? (
              <span className="flex items-center gap-2 normal-case tracking-normal">
                <Par par={dados.par} className="text-[12.5px]" />
                <span className="text-texto-3">{dados.timeframe}</span>
                <span className="text-texto-3">·</span>
                <span className="text-texto-2">{dados.setup_completo}</span>
              </span>
            ) : (
              "gráfico"
            )
          }
          meta={dados ? `última vela ${dataHora(dados.ultima_vela)} · fech ${preco(dados.preco)}` : undefined}
          acoes={
            <div className="flex flex-wrap gap-3">
              {todos
                ? todos.map((s, i) => (
                    <span key={s.nome} className="mono flex items-center gap-1.5 text-[10.5px] text-texto-2">
                      <Ponto cor={coresSetups[i % coresSetups.length]} /> {s.nome}
                    </span>
                  ))
                : legendaLinhas.map((l) => (
                    <span key={l.nome} className="mono flex items-center gap-1.5 text-[10.5px] text-texto-2">
                      <span className="inline-block h-px w-4" style={{ background: l.cor }} /> {l.nome}
                    </span>
                  ))}
              <span className="mono flex items-center gap-1.5 text-[10.5px] text-texto-3">
                {todos ? (
                  <>
                    ● entrada <span className="ml-1">■</span> saída (+ ganhou, − perdeu)
                  </>
                ) : (
                  <>
                    <span className="text-alta">▲</span> sinal <span className="ml-1 text-acento">●</span> entrada <span className="ml-1 text-texto-2">■</span> saída
                  </>
                )}
              </span>
            </div>
          }
          semRecheio
        >
          {carregando && !dados && (
            <div className="p-3">
              <Carregando texto="carregando velas e calculando o setup…" />
            </div>
          )}
          <div ref={ref} className="h-[520px] w-full" />
        </Painel>

        <div className="space-y-4">
          {todos ? (
            <Painel titulo="por setup" meta="nas velas mostradas" semRecheio>
              <Tabela>
                <thead>
                  <tr>
                    <th className={th}>setup</th>
                    <th className={thNum}>ops</th>
                    <th className={thNum} title="ganhas / perdidas, já fechadas">g/p</th>
                    <th className={thNum} title="soma dos retornos líquidos das operações fechadas">soma</th>
                    <th className={th}>última</th>
                  </tr>
                </thead>
                <tbody>
                  {todos.map((s, i) => {
                    const fechadas = s.operacoes.filter((o) => !o.aberto);
                    const ganhas = fechadas.filter((o) => o.retorno_liquido_pct > 0).length;
                    const soma = fechadas.reduce((acc, o) => acc + o.retorno_liquido_pct, 0);
                    const ultima = s.operacoes[s.operacoes.length - 1];
                    return (
                      <tr key={s.nome}>
                        <td className={`${td} mono`}>
                          <span className="flex items-center gap-1.5">
                            <Ponto cor={coresSetups[i % coresSetups.length]} /> {s.nome}
                          </span>
                        </td>
                        <td className={tdNum}>{s.operacoes.length || <span className="text-texto-3">—</span>}</td>
                        <td className={tdNum}>
                          {fechadas.length ? (
                            <>
                              <span className="text-alta">{ganhas}</span>/<span className="text-baixa">{fechadas.length - ganhas}</span>
                            </>
                          ) : (
                            <span className="text-texto-3">—</span>
                          )}
                        </td>
                        <td className={tdNum}>{fechadas.length ? <Delta valor={soma} casas={1} /> : <span className="text-texto-3">—</span>}</td>
                        <td className={td}>{ultima ? <Etiqueta tom={desfecho(ultima).tom}>{desfecho(ultima).rotulo}</Etiqueta> : <span className="mono text-[10.5px] text-texto-3">sem op</span>}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </Tabela>
            </Painel>
          ) : (
            <Painel titulo="último sinal" meta={u ? dataHora(u.quando) : undefined}>
              {!u ? (
                <Vazio texto="nenhum sinal nas velas mostradas" />
              ) : (
                <div className="space-y-3">
                  <div className="flex items-center justify-between">
                    <Etiqueta tom={u.direcao > 0 ? "alta" : "baixa"}>{lado(u.direcao)}</Etiqueta>
                    <span className="mono text-[11px] text-texto-3">força {pct(u.forca)}</span>
                  </div>
                  <div className="grid grid-cols-3 gap-2">
                    <Campo rotulo="fech. do sinal">{preco(u.preco)}</Campo>
                    <Campo rotulo="stop">
                      <span className="text-baixa">{preco(u.stop)}</span>
                      <div className="text-[10.5px] text-texto-3">{u.stop ? pct(u.stop / u.preco - 1, 1, true) : ""}</div>
                    </Campo>
                    <Campo rotulo="alvo">
                      <span className="text-alta">{preco(u.alvo)}</span>
                      <div className="text-[10.5px] text-texto-3">{u.alvo ? pct(u.alvo / u.preco - 1, 1, true) : ""}</div>
                    </Campo>
                  </div>
                  <p className="mono text-[11px] text-texto-2">
                    ganha/perde <span className="text-texto">{numero(razao, 2)}:1</span>
                  </p>
                  {u.motivo && <p className="text-[11.5px] text-texto-3">por quê: {u.motivo}</p>}

                  <div className="border-t border-borda pt-3">
                    <p className="eyebrow mb-1.5">o que ele virou</p>
                    {op ? (
                      <div className="space-y-2">
                        <div className="flex items-center justify-between gap-2">
                          <Etiqueta tom={desfecho(op).tom}>{desfecho(op).rotulo}</Etiqueta>
                          {!op.aberto && <Delta valor={op.retorno_liquido_pct} casas={2} />}
                        </div>
                        <dl className="mono space-y-1 text-[11px]">
                          <div className="flex justify-between gap-2">
                            <dt className="text-texto-3">entrou</dt>
                            <dd className="text-right">
                              {dataHora(op.entrada)} <span className="text-acento">@ {preco(op.preco_entrada)}</span>
                            </dd>
                          </div>
                          {op.aberto ? (
                            <div className="flex justify-between gap-2">
                              <dt className="text-texto-3">agora</dt>
                              <dd className="text-right">
                                {preco(op.preco_saida)} · <Delta valor={op.retorno_liquido_pct} casas={2} />
                              </dd>
                            </div>
                          ) : (
                            <div className="flex justify-between gap-2">
                              <dt className="text-texto-3">saiu</dt>
                              <dd className="text-right">
                                {dataHora(op.saida)} @ {preco(op.preco_saida)}
                              </dd>
                            </div>
                          )}
                          <div className="flex justify-between gap-2">
                            <dt className="text-texto-3">em R</dt>
                            <dd className={op.multiplo_r > 0 ? "text-alta" : op.multiplo_r < 0 ? "text-baixa" : ""}>
                              {numero(op.multiplo_r, 2)} R · {op.barras} vela{op.barras === 1 ? "" : "s"}
                            </dd>
                          </div>
                        </dl>
                        <p className="text-[11px] leading-relaxed text-texto-3">
                          A entrada é na abertura da vela seguinte ao sinal; no gráfico, o círculo azul e a linha "entrada". Resultado já com as taxas.
                        </p>
                      </div>
                    ) : (
                      <p className="text-[11.5px] leading-relaxed text-atencao">{dados?.motivo_sem_trade ?? "sem operação para este sinal"}</p>
                    )}
                  </div>
                </div>
              )}
            </Painel>
          )}

          <Painel titulo="operações nas velas mostradas" meta={`${combinadas.length} no total`} semRecheio>
            {combinadas.length === 0 ? (
              <div className="p-3">
                <Vazio texto="nenhuma" />
              </div>
            ) : (
              <ul className="max-h-80 overflow-y-auto">
                {combinadas.map((o, i) => {
                  const d = desfecho(o);
                  return (
                    <li key={i} className="flex items-center justify-between gap-2 border-b border-borda/70 px-3 py-1.5 last:border-b-0 hover:bg-painel-2">
                      <span className="mono flex items-center gap-1.5 text-[11px] text-texto-2">
                        {todos && <Ponto cor={corDoSetup(o.setup)} />}
                        {dataHora(o.entrada)} <span className={o.direcao > 0 ? "text-alta" : "text-baixa"}>{o.direcao > 0 ? "C" : "V"}</span>
                        {todos && <span className="text-texto-3">{o.setup}</span>}
                      </span>
                      <span className="flex items-center gap-2">
                        <Etiqueta tom={d.tom}>{d.rotulo}</Etiqueta>
                        <Delta valor={o.aberto ? null : o.retorno_liquido_pct} casas={2} className="w-16 text-right text-[11px]" />
                      </span>
                    </li>
                  );
                })}
              </ul>
            )}
          </Painel>
        </div>
      </div>
    </div>
  );
}
