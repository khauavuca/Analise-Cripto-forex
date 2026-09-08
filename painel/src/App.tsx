import { useEffect, useState } from "react";
import { Activity, BookOpen, CandlestickChart, LayoutGrid, Layers, ShieldCheck } from "lucide-react";
import { api, type Estado } from "./api";
import { usarDados } from "./usarDados";
import { data, hora, preco } from "./formato";
import { Delta, Etiqueta, Par, Ponto } from "./componentes";
import { VisaoGeral } from "./paginas/VisaoGeral";
import { Agora } from "./paginas/Agora";
import { Grafico } from "./paginas/Grafico";
import { Setups } from "./paginas/Setups";
import { ComoLer } from "./paginas/ComoLer";

const paginas = [
  { id: "visao", rotulo: "Visão geral", icone: LayoutGrid, eyebrow: "campanha", titulo: "Campanha de teste" },
  { id: "agora", rotulo: "Agora", icone: Activity, eyebrow: "boleta", titulo: "O que o sistema faria agora" },
  { id: "grafico", rotulo: "Gráfico", icone: CandlestickChart, eyebrow: "velas", titulo: "Gráfico com os sinais do setup" },
  { id: "setups", rotulo: "Setups", icone: Layers, eyebrow: "traders", titulo: "Os traders e o acerto medido ao vivo" },
  { id: "como-ler", rotulo: "Como ler", icone: BookOpen, eyebrow: "glossário", titulo: "Como ler os números" },
] as const;

type Rota = { pagina: string; parametros: URLSearchParams };

function lerRota(): Rota {
  const h = location.hash.replace(/^#\/?/, "");
  const [pagina, consulta = ""] = h.split("?");
  return { pagina: pagina || "visao", parametros: new URLSearchParams(consulta) };
}

export function irPara(pagina: string, parametros?: Record<string, string>) {
  const consulta = parametros ? `?${new URLSearchParams(parametros).toString()}` : "";
  location.hash = `#/${pagina}${consulta}`;
}

/** Dois relogios, como toda mesa: o de quem opera e o da corretora. */
function Relogio() {
  const [agora, setAgora] = useState(() => new Date());
  useEffect(() => {
    const id = setInterval(() => setAgora(new Date()), 1000);
    return () => clearInterval(id);
  }, []);
  const f = (tz: string) =>
    new Intl.DateTimeFormat("pt-BR", { hour: "2-digit", minute: "2-digit", second: "2-digit", timeZone: tz }).format(agora);
  return (
    <div className="mono flex items-center gap-3 text-[11.5px]">
      <span>
        <span className="text-texto-3">BRT </span>
        <span className="text-texto">{f("America/Sao_Paulo")}</span>
      </span>
      <span>
        <span className="text-texto-3">UTC </span>
        <span className="text-texto-2">{f("UTC")}</span>
      </span>
    </div>
  );
}

function Fita({ estado }: { estado: Estado }) {
  const linhas = estado.velas.filter((v) => v.timeframe === "1h" && v.fechamento !== null);
  if (linhas.length === 0) return null;
  return (
    <div className="flex min-w-0 items-center gap-5 overflow-x-auto">
      {linhas.map((v) => (
        <button
          key={v.par}
          type="button"
          onClick={() => irPara("grafico", { par: v.par, tf: "4h" })}
          className="flex shrink-0 items-baseline gap-2 rounded-term px-1 py-0.5 hover:bg-painel-2"
          title={`última vela de 1h ${hora(v.ultima)} · variação em 24h`}
        >
          <Par par={v.par} className="text-[11.5px]" />
          <span className="mono text-[12px] text-texto">{preco(v.fechamento)}</span>
          <Delta valor={v.variacao_24h} casas={1} className="text-[11px]" />
        </button>
      ))}
    </div>
  );
}

export function App() {
  const [rota, setRota] = useState<Rota>(lerRota);
  useEffect(() => {
    const ao = () => setRota(lerRota());
    addEventListener("hashchange", ao);
    return () => removeEventListener("hashchange", ao);
  }, []);

  const estado = usarDados(() => api.estado(), "estado");
  const e = estado.dados;
  const { erro: erroEstado, recarregar: recarregarEstado } = estado;

  // Mesa viva: a fita e o "dados ate" se renovam sozinhos; se a API caiu
  // (ou ainda estava subindo), tenta de novo em vez de ficar em "conectando".
  useEffect(() => {
    const id = setInterval(() => void recarregarEstado(false), erroEstado ? 5_000 : 30_000);
    return () => clearInterval(id);
  }, [erroEstado, recarregarEstado]);
  const atual = paginas.find((p) => p.id === rota.pagina) ?? paginas[0];
  const ultima1h = e?.velas.find((v) => v.timeframe === "1h" && v.ultima)?.ultima ?? null;

  return (
    <div className="flex h-full min-h-screen flex-col">
      <header className="flex h-11 shrink-0 items-center gap-6 border-b border-borda bg-painel px-4">
        <div className="flex shrink-0 items-baseline gap-2">
          <span className="mono text-[13px] font-semibold tracking-[0.18em] text-texto">MESA</span>
          <span className="eyebrow">análise cripto</span>
        </div>
        {e ? <Fita estado={e} /> : <span className="mono text-[11px] text-texto-3">conectando…</span>}
        <div className="ml-auto flex shrink-0 items-center gap-5">
          {e && (
            <span className="flex items-center gap-2" title={e.rede ? "Buscando velas novas na corretora" : "Sem rede: só o que está no banco"}>
              <Ponto tom={e.rede ? "alta" : "atencao"} pulsar={e.rede} />
              <span className="mono text-[11px] uppercase tracking-wider text-texto-2">
                {e.corretora} {e.rede ? "ao vivo" : "offline"}
              </span>
              {ultima1h && <span className="mono hidden text-[11px] text-texto-3 xl:inline">dados até {hora(ultima1h)}</span>}
            </span>
          )}
          <Relogio />
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        <aside className="flex w-44 shrink-0 flex-col border-r border-borda bg-painel">
          <nav className="flex flex-col py-2">
            {paginas.map((p) => {
              const Icone = p.icone;
              const ativo = p.id === atual.id;
              return (
                <button
                  key={p.id}
                  type="button"
                  onClick={() => irPara(p.id)}
                  className={`flex items-center gap-2.5 border-l-2 px-3.5 py-2 text-left text-[12.5px] transition-colors ${
                    ativo ? "border-l-acento bg-painel-2 text-texto" : "border-l-transparent text-texto-2 hover:bg-painel-2/60 hover:text-texto"
                  }`}
                >
                  <Icone size={15} className={ativo ? "text-acento" : "text-texto-3"} /> {p.rotulo}
                </button>
              );
            })}
          </nav>
          <div className="mt-auto space-y-2 border-t border-borda px-3.5 py-3">
            {e && (
              <div className="space-y-1">
                <p className="eyebrow">campanha</p>
                <p className="mono text-[11px] text-texto-2">
                  {data(e.campanha.inicio)} → {data(e.campanha.fim)}
                </p>
                <Etiqueta tom={e.campanha.encerrada ? "neutro" : e.campanha.dias_restantes === 0 ? "atencao" : "info"}>
                  {e.campanha.encerrada ? "encerrada" : e.campanha.dias_restantes === 0 ? "último dia" : `faltam ${e.campanha.dias_restantes} dias`}
                </Etiqueta>
              </div>
            )}
            <p className="flex items-center gap-1.5 pt-1 text-[11px] text-alta">
              <ShieldCheck size={12} /> nenhuma ordem é enviada
            </p>
            <p className="mono text-[10px] text-texto-3">
              {e ? `${e.rotulo_fuso} · v${e.versao}` : "…"}
            </p>
          </div>
        </aside>

        <main className="min-w-0 flex-1 overflow-y-auto">
          <div className="flex flex-wrap items-end justify-between gap-3 border-b border-borda px-6 py-3">
            <div>
              <p className="eyebrow">{atual.eyebrow}</p>
              <h1 className="mt-0.5 text-[17px] font-semibold tracking-tight">{atual.titulo}</h1>
            </div>
            {e && (
              <p className="mono text-[11px] text-texto-3">
                {e.pares.map((p) => p.replace("/USDT", "")).join(" · ")} · {e.timeframes.join(" / ")} · {e.setups.length} setups · banca {e.campanha.moeda}{" "}
                {e.campanha.banca} por trader
              </p>
            )}
          </div>
          <div className="px-6 py-5">
            {atual.id === "visao" && <VisaoGeral estado={e} />}
            {atual.id === "agora" && <Agora estado={e} />}
            {atual.id === "grafico" && <Grafico estado={e} parametros={rota.parametros} />}
            {atual.id === "setups" && <Setups estado={e} />}
            {atual.id === "como-ler" && <ComoLer estado={e} />}
          </div>
        </main>
      </div>
    </div>
  );
}
