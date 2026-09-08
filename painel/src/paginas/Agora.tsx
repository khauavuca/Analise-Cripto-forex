import { useState } from "react";
import { CandlestickChart } from "lucide-react";
import { api, type Estado } from "../api";
import { usarDados } from "../usarDados";
import { dataHora, moeda, numero, pct, preco } from "../formato";
import { Aviso, Botao, BotaoAtualizar, Carregando, Erro, Etiqueta, Painel, Par, Tabela, Vazio, td, tdNum, th, thNum } from "../componentes";
import { irPara } from "../App";

function velaCurta(vela: string): string {
  // "2026-09-04 17:00" -> "04/09 17:00"
  const m = vela.match(/^(\d{4})-(\d{2})-(\d{2}) (\d{2}:\d{2})$/);
  return m ? `${m[3]}/${m[2]} ${m[4]}` : vela;
}

export function Agora({ estado }: { estado: Estado | null }) {
  const bancaPadrao = estado?.campanha.banca ?? 500;
  const moedaPadrao = estado?.campanha.moeda ?? "BRL";
  const [banca, setBanca] = useState<number>(bancaPadrao);
  const [bancaAplicada, setBancaAplicada] = useState<number>(bancaPadrao);
  const { dados, erro, carregando, recarregar } = usarDados((a) => api.decisao(bancaAplicada, moedaPadrao, a), `decisao:${bancaAplicada}:${moedaPadrao}`);
  const m = dados?.moeda ?? moedaPadrao;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="flex items-end gap-2">
          <label className="eyebrow">
            banca ({moedaPadrao})
            <input
              type="number"
              min={10}
              step={50}
              value={banca}
              onChange={(ev) => setBanca(Number(ev.target.value))}
              className="mono mt-1 block h-7 w-28 rounded-term border border-borda-2 bg-painel-2 px-2 text-[12.5px] text-texto outline-none focus:border-acento"
            />
          </label>
          <Botao onClick={() => setBancaAplicada(banca)} desabilitado={banca === bancaAplicada || banca <= 0}>
            aplicar
          </Botao>
        </div>
        <BotaoAtualizar onClick={() => recarregar(true)} carregando={carregando} />
      </div>

      {dados && (
        <p className="mono text-[11px] text-texto-3">
          regras · risco {pct(dados.regras.risco_por_trade)}/op · máx {dados.regras.max_posicoes} posições · {dados.regras.max_por_par} por par · exposição{" "}
          {pct(dados.regras.exposicao_maxima)} · perda diária {pct(dados.regras.perda_diaria_maxima)} · lido {dataHora(dados.gerado_em)}
        </p>
      )}

      {erro && <Erro erro={erro} tentar={() => recarregar(false)} />}
      {!dados && !erro && <Carregando texto="lendo a última vela fechada de cada par e passando pelos setups…" />}

      {dados && (
        <Painel
          titulo="boleta"
          meta={
            dados.recomendacoes.length === 0
              ? "nenhum sinal ativo"
              : `${dados.entrar} entraria · ${dados.recomendacoes.length - dados.entrar} recusada${dados.recomendacoes.length - dados.entrar === 1 ? "" : "s"}`
          }
          semRecheio
        >
          {dados.recomendacoes.length === 0 ? (
            <div className="p-3">
              <Vazio texto="nenhum sinal ativo na última vela fechada de nenhum par — normal: os setups falam pouco de propósito" />
            </div>
          ) : (
            <Tabela>
              <thead>
                <tr>
                  <th className={th}>decisão</th>
                  <th className={th}>par</th>
                  <th className={th}>lado</th>
                  <th className={th}>setup</th>
                  <th className={th}>vela</th>
                  <th className={thNum}>força</th>
                  <th className={thNum}>preço</th>
                  <th className={thNum}>stop</th>
                  <th className={thNum}>alvo</th>
                  <th className={thNum} title="Quanto ganha se bater o alvo, para cada 1 que perde se bater o stop">g/p</th>
                  <th className={thNum}>ordem</th>
                  <th className={thNum}>risco</th>
                  <th className={th}>motivo</th>
                  <th className={th}></th>
                </tr>
              </thead>
              <tbody>
                {dados.recomendacoes.map((r, i) => {
                  const entrar = r.decisao === "ENTRAR";
                  return (
                    <tr key={`${r.par}-${r.estrategia}-${r.timeframe}-${i}`}>
                      <td className={td}>
                        <span className="flex items-center gap-2">
                          <span className={`inline-block h-4 w-0.5 ${entrar ? "bg-alta" : "bg-atencao"}`} />
                          <Etiqueta tom={entrar ? "alta" : "atencao"} titulo={entrar ? "Passou pelas regras da banca" : r.motivo_recusa}>
                            {entrar ? "entraria" : "recusada"}
                          </Etiqueta>
                        </span>
                      </td>
                      <td className={td}>
                        <Par par={r.par} /> <span className="mono text-[10.5px] text-texto-3">{r.timeframe}</span>
                      </td>
                      <td className={td}>
                        <Etiqueta tom={r.direcao > 0 ? "alta" : "baixa"}>{r.lado}</Etiqueta>
                      </td>
                      <td className={`${td} mono`}>{r.nome_curto}</td>
                      <td className={`${td} mono text-texto-2`}>{velaCurta(r.vela)}</td>
                      <td className={tdNum}>{pct(r.forca)}</td>
                      <td className={tdNum}>{preco(r.preco)}</td>
                      <td className={tdNum}>
                        <span className="text-baixa">{preco(r.stop)}</span>
                        <div className="text-[10.5px] text-texto-3">{pct(r.stop_pct, 1, true)}</div>
                      </td>
                      <td className={tdNum}>
                        <span className="text-alta">{preco(r.alvo)}</span>
                        <div className="text-[10.5px] text-texto-3">{pct(r.alvo_pct, 1, true)}</div>
                      </td>
                      <td className={tdNum}>{numero(r.razao_risco_retorno, 2)}:1</td>
                      <td className={tdNum}>{entrar ? moeda(r.valor_ordem, m) : <span className="text-texto-3">—</span>}</td>
                      <td className={`${tdNum} ${entrar ? "text-baixa" : "text-texto-3"}`}>{entrar ? moeda(r.risco_moeda, m) : "—"}</td>
                      <td className={`${td} max-w-64 truncate text-texto-2`} title={entrar ? r.motivo : `${r.motivo_recusa} · ${r.motivo}`}>
                        {entrar ? r.motivo : r.motivo_recusa}
                        {r.leituras?.length > 0 && (
                          <div className="mono flex flex-wrap gap-1 pt-1 text-[10px]">
                            {r.leituras.map((l) => (
                              <span
                                key={l}
                                className={`rounded-term border px-1 ${
                                  l.endsWith("a favor") ? "border-alta/30 text-alta" : l.endsWith("contra") ? "border-baixa/30 text-baixa" : "border-borda-2 text-texto-3"
                                }`}
                              >
                                {l}
                              </span>
                            ))}
                          </div>
                        )}
                      </td>
                      <td className={td}>
                        <Botao pequeno onClick={() => irPara("grafico", { par: r.par, tf: r.timeframe, setup: r.nome_curto })} titulo="Ver no gráfico">
                          <CandlestickChart size={12} />
                        </Botao>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </Tabela>
          )}
        </Painel>
      )}

      <Aviso tom="info" rotulo="leitura" titulo="Recomendação é o registro do que o sistema faria. Nenhuma ordem foi enviada.">
        Cada linha vem de um setup olhando a última vela fechada. "Entraria" quer dizer que passou pelas regras da banca; "recusada" mostra qual regra barrou. O
        tamanho da ordem sai do risco de {dados ? pct(dados.regras.risco_por_trade) : "2%"} sobre a banca e da distância até o stop.
      </Aviso>
    </div>
  );
}
