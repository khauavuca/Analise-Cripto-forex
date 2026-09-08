import { api, type Estado } from "../api";
import { usarDados } from "../usarDados";
import { dataHora, lado, numero, pct, preco } from "../formato";
import { Aviso, BotaoAtualizar, Carregando, Erro, Etiqueta, Painel, Par, Tabela, Vazio, td, tdNum, th, thNum } from "../componentes";

const motivos: Record<string, string> = {
  ALVO: "alvo",
  STOP: "stop",
  TEMPO: "tempo",
  FIM_DADOS: "em aberto",
};

export function Setups({ estado }: { estado: Estado | null }) {
  const { dados, erro, carregando, recarregar } = usarDados((a) => api.rastreio(a), "rastreio");
  const setups = estado?.setups ?? [];

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="max-w-3xl text-[12.5px] text-texto-2">
          Cada setup é um jeito diferente de decidir. Aqui está o que cada um faz e, embaixo, o acerto medido com os sinais que a nuvem coletou ao vivo, seguidos
          até o stop ou o alvo.
        </p>
        <BotaoAtualizar onClick={() => recarregar(true)} carregando={carregando} />
      </div>

      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        {setups.map((s) => {
          const r = dados?.setups.find((x) => x.nome_curto === s.nome);
          return (
            <div key={s.nome} className="rounded-term border border-borda bg-painel px-3 py-2.5">
              <div className="flex items-center justify-between gap-2">
                <p className="mono text-[12.5px] font-medium">{s.nome}</p>
                {r ? (
                  <Etiqueta tom={r.amostra_ok ? "info" : "atencao"} titulo={r.amostra_ok ? "Já há operações suficientes para levar o número a sério" : "Poucas operações: o número ainda é chute"}>
                    {r.fechados} fechada{r.fechados === 1 ? "" : "s"}
                  </Etiqueta>
                ) : (
                  <Etiqueta tom="neutro">sem sinal</Etiqueta>
                )}
              </div>
              <p className="mt-1 text-[12px] leading-snug text-texto-2">{s.descricao}</p>
              <p className="mono mt-1.5 truncate text-[10.5px] text-texto-3" title={s.completo}>
                {s.completo}
              </p>
            </div>
          );
        })}
      </div>

      {erro && <Erro erro={erro} tentar={() => recarregar(false)} />}
      {!dados && !erro && <Carregando texto="reconstruindo o desfecho de cada sinal coletado pelo motor do backtest…" />}

      {dados && (
        <>
          <Painel
            titulo="acerto medido ao vivo"
            meta={`${dados.observacoes} observações · ${dados.sinais} sinais com stop e alvo · ${dados.fechados ?? 0} fecharam · ${dados.abertos ?? 0} em aberto${dados.gerado_em ? ` · lido ${dataHora(dados.gerado_em)}` : ""}`}
            semRecheio
          >
            {dados.setups.length === 0 ? (
              <div className="p-3">
                <Vazio texto="nenhum sinal com desfecho ainda; a nuvem coleta a cada execução" />
              </div>
            ) : (
              <Tabela>
                <thead>
                  <tr>
                    <th className={th}>setup</th>
                    <th className={thNum}>fechadas</th>
                    <th className={thNum}>abertas</th>
                    <th className={thNum} title="Fatia dos sinais fechados que deram lucro, e a faixa provável">acerto</th>
                    <th className={thNum} title="Ganho médio dividido pela perda média">payoff</th>
                    <th className={thNum} title="Acerto mínimo para não perder dinheiro com esse payoff">empata em</th>
                    <th className={thNum} title="Quanto cada operação rende em média, em R (1 R = o que se arriscou até o stop)">média em R</th>
                    <th className={thNum} title="Retorno líquido médio por operação, em % do preço">retorno médio</th>
                    <th className={th}>amostra</th>
                  </tr>
                </thead>
                <tbody>
                  {dados.setups.map((r) => {
                    const acima = r.acerto !== null && r.acerto_para_empatar !== null && r.acerto > r.acerto_para_empatar;
                    return (
                      <tr key={r.estrategia}>
                        <td className={td}>
                          <div className="mono font-medium">{r.nome_curto}</div>
                          <div className="max-w-56 truncate text-[10.5px] text-texto-3">{r.estrategia}</div>
                        </td>
                        <td className={tdNum}>
                          {r.fechados}
                          <div className="text-[10.5px] text-texto-3">
                            <span className="text-alta">{r.ganhos}</span>/<span className="text-baixa">{r.perdidos}</span>
                          </div>
                        </td>
                        <td className={tdNum}>{r.abertos || <span className="text-texto-3">—</span>}</td>
                        <td className={tdNum}>
                          {r.fechados ? (
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
                        <td className={tdNum}>{r.payoff === null ? <span className="text-texto-3">—</span> : numero(r.payoff, 2)}</td>
                        <td className={tdNum}>{r.acerto_para_empatar === null ? <span className="text-texto-3">—</span> : pct(r.acerto_para_empatar)}</td>
                        <td className={`${tdNum} ${r.expectancia_r === null ? "" : r.expectancia_r > 0 ? "text-alta" : "text-baixa"}`}>
                          {r.expectancia_r === null ? "—" : `${numero(r.expectancia_r, 2)} R`}
                        </td>
                        <td className={`${tdNum} ${r.retorno_medio === null ? "" : r.retorno_medio > 0 ? "text-alta" : "text-baixa"}`}>{pct(r.retorno_medio, 2, true)}</td>
                        <td className={td}>
                          <Etiqueta tom={r.amostra_ok ? "info" : "atencao"}>{r.amostra_ok ? "ok" : "curta"}</Etiqueta>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </Tabela>
            )}
          </Painel>

          {dados.veredito && (
            <Aviso tom="info" rotulo="veredito do rastreador" titulo="">
              <pre className="whitespace-pre-wrap font-sans text-[12.5px]">{dados.veredito}</pre>
            </Aviso>
          )}

          <Painel titulo="últimos sinais seguidos até o desfecho" meta="cada linha é um sinal coletado ao vivo, acompanhado vela a vela pelo mesmo motor do backtest" semRecheio>
            {dados.trades.length === 0 ? (
              <div className="p-3">
                <Vazio texto="nenhum ainda" />
              </div>
            ) : (
              <Tabela>
                <thead>
                  <tr>
                    <th className={th}>entrada</th>
                    <th className={th}>setup</th>
                    <th className={th}>par</th>
                    <th className={th}>lado</th>
                    <th className={thNum}>preço</th>
                    <th className={thNum}>stop</th>
                    <th className={thNum}>alvo</th>
                    <th className={th}>desfecho</th>
                    <th className={thNum}>resultado</th>
                    <th className={thNum} title="Resultado medido em R: 1 R = o que se arriscou">r</th>
                  </tr>
                </thead>
                <tbody>
                  {dados.trades.map((t, i) => (
                    <tr key={i}>
                      <td className={`${td} mono text-texto-2`}>{dataHora(t.entrada)}</td>
                      <td className={`${td} mono`}>{t.nome_curto}</td>
                      <td className={td}>
                        <Par par={t.par} /> <span className="mono text-[10.5px] text-texto-3">{t.timeframe}</span>
                      </td>
                      <td className={td}>
                        <Etiqueta tom={t.direcao > 0 ? "alta" : "baixa"}>{lado(t.direcao)}</Etiqueta>
                      </td>
                      <td className={tdNum}>{preco(t.preco_entrada)}</td>
                      <td className={`${tdNum} text-baixa`}>{preco(t.stop)}</td>
                      <td className={`${tdNum} text-alta`}>{preco(t.alvo)}</td>
                      <td className={td}>
                        {t.aberto ? (
                          <Etiqueta tom="neutro">em aberto</Etiqueta>
                        ) : (
                          <Etiqueta tom={t.motivo_saida === "ALVO" ? "alta" : t.motivo_saida === "STOP" ? "baixa" : "atencao"}>{motivos[t.motivo_saida] ?? t.motivo_saida}</Etiqueta>
                        )}
                      </td>
                      <td className={`${tdNum} ${t.aberto ? "text-texto-3" : t.retorno_liquido_pct && t.retorno_liquido_pct > 0 ? "text-alta" : "text-baixa"}`}>
                        {t.aberto ? "—" : pct(t.retorno_liquido_pct, 2, true)}
                      </td>
                      <td className={`${tdNum} ${t.aberto ? "text-texto-3" : t.multiplo_r && t.multiplo_r > 0 ? "text-alta" : "text-baixa"}`}>
                        {t.aberto ? "—" : numero(t.multiplo_r, 2)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </Tabela>
            )}
          </Painel>
        </>
      )}
    </div>
  );
}
