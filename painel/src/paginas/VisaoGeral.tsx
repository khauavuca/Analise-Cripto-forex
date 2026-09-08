import { useMemo, useState } from "react";
import { CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, type Campanha, type Estado } from "../api";
import { usarDados } from "../usarDados";
import { classeSinal, data, dataHora, fusoDe, lado, moeda, numero, pct, preco } from "../formato";
import { Aviso, BotaoAtualizar, Carregando, Delta, Erro, Etiqueta, Ladrilho, Painel, Par, Sparkline, Tabela, Vazio, td, tdNum, th, thNum } from "../componentes";

const cores = ["#4d8dff", "#19c37d", "#f5b83d", "#f0435a", "#22c1c3", "#c084fc", "#f472b6", "#a3e635", "#94a3b8"];

function montarSerie(curvas: Campanha["curvas"]) {
  const tempos = new Set<number>();
  for (const pontos of Object.values(curvas)) for (const p of pontos) tempos.add(new Date(p.t).getTime());
  const ordenados = [...tempos].sort((a, b) => a - b);
  const nomes = Object.keys(curvas);
  const indices: Record<string, number> = {};
  const ultimos: Record<string, number | null> = {};
  return ordenados.map((t) => {
    const linha: Record<string, number | null> = { t };
    for (const nome of nomes) {
      const pontos = curvas[nome];
      let i = indices[nome] ?? 0;
      while (i < pontos.length && new Date(pontos[i].t).getTime() <= t) {
        ultimos[nome] = pontos[i].saldo;
        i++;
      }
      indices[nome] = i;
      linha[nome] = ultimos[nome] ?? null;
    }
    return linha;
  });
}

export function VisaoGeral({ estado }: { estado: Estado | null }) {
  const { dados, erro, carregando, recarregar } = usarDados((a) => api.campanha(a), "campanha");
  const [mostrarTexto, setMostrarTexto] = useState(false);

  const serie = useMemo(() => (dados ? montarSerie(dados.curvas) : []), [dados]);
  const fuso = dados ? fusoDe(dados.gerado_em) : "UTC";
  const horaEixo = (ms: number) =>
    new Intl.DateTimeFormat("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", timeZone: fuso }).format(new Date(ms));

  if (erro) return <Erro erro={erro} tentar={() => recarregar(false)} />;
  if (!dados) return <Carregando texto="refazendo a campanha a partir das velas reais… (uns segundos)" />;

  const c = dados;
  const m = c.moeda;
  const nomeCurto = new Map(c.ranking.map((r) => [r.trader, r.nome_curto]));
  const lider = c.ranking[0];
  const fechadasTotal = c.ranking.reduce((s, r) => s + r.operacoes, 0);
  const abertasTotal = c.ranking.reduce((s, r) => s + r.em_aberto, 0);
  const positivos = c.ranking.filter((r) => r.resultado > 0).length;
  const piorQueda = Math.min(0, ...c.ranking.map((r) => r.maior_queda));

  const fechadas = Object.entries(c.traders)
    .flatMap(([trader, t]) => t.fechadas.map((f) => ({ ...f, trader })))
    .sort((a, b) => new Date(b.momento).getTime() - new Date(a.momento).getTime())
    .slice(0, 12);
  const abertas = Object.entries(c.traders).flatMap(([trader, t]) => t.abertas.map((a) => ({ ...a, trader })));

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="mono text-[11.5px] text-texto-2">
          <span className="text-texto">{data(c.inicio)}</span> → <span className="text-texto">{data(c.fim_inclusivo ?? c.fim)}</span>
          <span className="text-texto-3"> · </span>
          {c.pares.length} pares · {c.timeframes.join(" / ")} · atualizado {dataHora(c.gerado_em)}
          {c.ultima_vela && <span className="text-texto-3"> · última vela {dataHora(c.ultima_vela)}</span>}
        </p>
        <BotaoAtualizar onClick={() => recarregar(true)} carregando={carregando} />
      </div>

      {c.cedo && (
        <Aviso rotulo="amostra curta" titulo={`Nenhum trader passou de ${c.minimo_para_opinar} operações fechadas (o máximo foi ${c.maximo_operacoes}).`}>
          Com poucas operações a ordem abaixo é sorte, não habilidade. A campanha existe para acumular operações; a ordem só vale quando a amostra crescer.
        </Aviso>
      )}

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <Ladrilho
          rotulo="líder"
          valor={lider ? lider.nome_curto : "—"}
          sub={lider ? moeda(lider.banca_atual, m) : undefined}
          tom={lider && lider.resultado > 0 ? "alta" : lider && lider.resultado < 0 ? "baixa" : "neutro"}
          extra={lider ? <Delta valor={lider.variacao} casas={1} className="text-[11px]" /> : undefined}
        />
        <Ladrilho rotulo="no positivo" valor={`${positivos}/${c.ranking.length}`} sub={`começaram com ${moeda(c.banca_inicial, m)}`} tom={positivos > c.ranking.length / 2 ? "alta" : "neutro"} />
        <Ladrilho rotulo="operações fechadas" valor={fechadasTotal} sub={`${abertasTotal} em aberto`} />
        <Ladrilho rotulo="pior queda" valor={piorQueda < 0 ? pct(piorQueda, 1) : "0%"} sub="da maior banca ao fundo" tom={piorQueda < -0.1 ? "baixa" : "neutro"} />
        <Ladrilho rotulo="velas no período" valor={numero(c.velas_no_periodo, 0)} sub={estado ? estado.rotulo_fuso : undefined} />
      </div>

      <Painel titulo="curva de banca" meta="cada linha é um setup com a própria banca · dinheiro de mentira, mercado de verdade" semRecheio>
        {serie.length < 2 ? (
          <div className="p-3">
            <Vazio texto="sem operação fechada ainda; a curva nasce com a primeira" />
          </div>
        ) : (
          <div className="h-72 px-2 pt-3">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={serie} margin={{ top: 4, right: 12, bottom: 0, left: 0 }}>
                <CartesianGrid stroke="#161e29" />
                <XAxis
                  dataKey="t"
                  type="number"
                  domain={["dataMin", "dataMax"]}
                  tickFormatter={horaEixo}
                  stroke="#29343f"
                  tick={{ fill: "#59647a", fontSize: 10.5, fontFamily: "JetBrains Mono, monospace" }}
                  minTickGap={48}
                />
                <YAxis
                  stroke="#29343f"
                  tick={{ fill: "#59647a", fontSize: 10.5, fontFamily: "JetBrains Mono, monospace" }}
                  tickFormatter={(v: number) => numero(v, 0)}
                  width={56}
                  domain={["auto", "auto"]}
                />
                <Tooltip
                  contentStyle={{ background: "#0b0f15", border: "1px solid #29343f", borderRadius: 2, fontSize: 11, fontFamily: "JetBrains Mono, monospace" }}
                  labelFormatter={(v) => horaEixo(Number(v))}
                  formatter={(v, nome) => [moeda(Number(v), m), nomeCurto.get(String(nome)) ?? String(nome)]}
                />
                <Legend formatter={(v) => nomeCurto.get(String(v)) ?? String(v)} wrapperStyle={{ fontSize: 11, fontFamily: "JetBrains Mono, monospace" }} />
                <ReferenceLine y={c.banca_inicial} stroke="#29343f" strokeDasharray="3 3" />
                {Object.keys(c.curvas).map((nome, i) => (
                  <Line key={nome} type="stepAfter" dataKey={nome} stroke={cores[i % cores.length]} dot={false} strokeWidth={1.5} connectNulls isAnimationActive={false} />
                ))}
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
      </Painel>

      <Painel titulo="ranking" meta="ordenado pela banca atual · passe o mouse nos títulos" semRecheio>
        <Tabela>
          <thead>
            <tr>
              <th className={th}>#</th>
              <th className={th}>trader</th>
              <th className={th} title="Banca ao longo da campanha; a linha tracejada é a banca inicial">curva</th>
              <th className={thNum}>banca</th>
              <th className={thNum}>resultado</th>
              <th className={thNum} title="Operações fechadas: ganhas / perdidas">ops</th>
              <th className={thNum} title="Fatia das operações que fecharam com lucro. Embaixo, a faixa em que o acerto verdadeiro provavelmente está.">acerto</th>
              <th className={thNum} title="Ganho médio dividido pela perda média. 2,00 = cada ganho paga duas perdas.">payoff</th>
              <th className={thNum} title="Acerto mínimo para não perder dinheiro com esse payoff. Bom é acertar acima disso com folga.">empata em</th>
              <th className={thNum} title="Quanto cada operação rendeu em média, depois das taxas">média/op</th>
              <th className={thNum}>abertas</th>
              <th className={thNum} title="Quanto a banca chegou a cair do seu ponto mais alto">dd</th>
            </tr>
          </thead>
          <tbody>
            {c.ranking.map((r, i) => {
              const acima = r.acerto !== null && r.acerto_para_empatar !== null && r.acerto > r.acerto_para_empatar;
              const curva = (c.curvas[r.trader] ?? []).map((p) => p.saldo);
              return (
                <tr key={r.trader}>
                  <td className={`${td} mono text-texto-3`}>{i + 1}</td>
                  <td className={td}>
                    <div className="mono font-medium">{r.nome_curto}</div>
                    <div className="max-w-56 truncate text-[11px] text-texto-3" title={r.trader}>
                      {r.descricao || r.trader}
                    </div>
                  </td>
                  <td className={td}>
                    <Sparkline valores={curva} base={c.banca_inicial} />
                  </td>
                  <td className={`${tdNum} font-medium`}>{moeda(r.banca_atual, m)}</td>
                  <td className={tdNum}>
                    <Delta valor={r.resultado} tipo="moeda" moeda={m} />
                    <div className="text-[10.5px]">
                      <Delta valor={r.variacao} casas={1} className="opacity-80" />
                    </div>
                  </td>
                  <td className={tdNum}>
                    {r.operacoes}
                    <div className="text-[10.5px] text-texto-3">
                      <span className="text-alta">{r.ganhas}</span>/<span className="text-baixa">{r.perdidas}</span>
                    </div>
                  </td>
                  <td className={tdNum}>
                    {r.operacoes ? (
                      <>
                        <span className={acima ? "text-alta" : r.acerto_para_empatar !== null ? "text-baixa" : ""}>{pct(r.acerto)}</span>
                        <div className="text-[10.5px] text-texto-3">
                          {pct(r.acerto_de)}–{pct(r.acerto_ate)}
                        </div>
                      </>
                    ) : (
                      <span className="text-texto-3">—</span>
                    )}
                  </td>
                  <td className={tdNum}>
                    {r.operacoes === 0 ? (
                      <span className="text-texto-3">—</span>
                    ) : r.payoff === null ? (
                      <Etiqueta tom="info">só ganhos</Etiqueta>
                    ) : r.payoff === 0 ? (
                      <Etiqueta tom="baixa">só perdas</Etiqueta>
                    ) : (
                      numero(r.payoff, 2)
                    )}
                  </td>
                  <td className={tdNum}>{r.payoff && r.payoff > 0 ? pct(r.acerto_para_empatar) : <span className="text-texto-3">—</span>}</td>
                  <td className={`${tdNum} ${classeSinal(r.media_por_operacao)}`}>{r.operacoes ? moeda(r.media_por_operacao, m, true) : <span className="text-texto-3">—</span>}</td>
                  <td className={tdNum}>{r.em_aberto || <span className="text-texto-3">—</span>}</td>
                  <td className={`${tdNum} ${r.maior_queda < 0 ? "text-baixa" : "text-texto-3"}`}>{r.maior_queda < 0 ? pct(r.maior_queda, 1) : "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </Tabela>
      </Painel>

      <div className="grid gap-4 xl:grid-cols-2">
        <Painel titulo="posições em aberto" meta="ainda não bateram nem o alvo nem o stop" semRecheio>
          {abertas.length === 0 ? (
            <div className="p-3">
              <Vazio texto="nenhuma posição em aberto" />
            </div>
          ) : (
            <Tabela>
              <thead>
                <tr>
                  <th className={th}>trader</th>
                  <th className={th}>par</th>
                  <th className={th}>lado</th>
                  <th className={th}>entrada</th>
                  <th className={thNum}>preço</th>
                  <th className={thNum}>stop</th>
                  <th className={thNum}>alvo</th>
                </tr>
              </thead>
              <tbody>
                {abertas.map((a, i) => (
                  <tr key={i}>
                    <td className={`${td} mono`}>{nomeCurto.get(a.trader) ?? a.trader}</td>
                    <td className={td}>
                      <Par par={a.par} /> <span className="mono text-[10.5px] text-texto-3">{a.timeframe}</span>
                    </td>
                    <td className={td}>
                      <Etiqueta tom={a.direcao > 0 ? "alta" : "baixa"}>{lado(a.direcao)}</Etiqueta>
                    </td>
                    <td className={`${td} mono text-texto-2`}>{dataHora(a.entrada)}</td>
                    <td className={tdNum}>{preco(a.preco_entrada)}</td>
                    <td className={`${tdNum} text-baixa`}>{preco(a.stop)}</td>
                    <td className={`${tdNum} text-alta`}>{preco(a.alvo)}</td>
                  </tr>
                ))}
              </tbody>
            </Tabela>
          )}
        </Painel>

        <Painel titulo="últimas operações fechadas" meta="resultado já com as taxas descontadas" semRecheio>
          {fechadas.length === 0 ? (
            <div className="p-3">
              <Vazio texto="nenhuma operação fechada ainda" />
            </div>
          ) : (
            <Tabela>
              <thead>
                <tr>
                  <th className={th}>quando</th>
                  <th className={th}>trader</th>
                  <th className={th}>par</th>
                  <th className={thNum}>valor</th>
                  <th className={thNum}>resultado</th>
                  <th className={thNum}>banca depois</th>
                </tr>
              </thead>
              <tbody>
                {fechadas.map((f, i) => (
                  <tr key={i}>
                    <td className={`${td} mono text-texto-2`}>{dataHora(f.momento)}</td>
                    <td className={`${td} mono`}>{nomeCurto.get(f.trader) ?? f.trader}</td>
                    <td className={td}>
                      <Par par={f.par} />
                    </td>
                    <td className={tdNum}>{moeda(f.valor, m)}</td>
                    <td className={tdNum}>
                      <Delta valor={f.resultado} tipo="moeda" moeda={m} />
                    </td>
                    <td className={tdNum}>{moeda(f.saldo, m)}</td>
                  </tr>
                ))}
              </tbody>
            </Tabela>
          )}
        </Painel>
      </div>

      <Painel
        titulo="relatório em texto"
        meta="o mesmo que a nuvem grava em dados/campanha/relatorio.md"
        acoes={
          <button type="button" onClick={() => setMostrarTexto((v) => !v)} className="mono text-[11px] uppercase tracking-wider text-acento hover:underline">
            {mostrarTexto ? "esconder" : "mostrar"}
          </button>
        }
      >
        {mostrarTexto ? (
          <pre className="mono overflow-x-auto whitespace-pre-wrap text-[11.5px] leading-relaxed text-texto-2">{c.relatorio}</pre>
        ) : (
          <p className="text-[12px] text-texto-3">A versão em texto traz a explicação de cada número, escrita para quem não é do ramo.</p>
        )}
      </Painel>
    </div>
  );
}
