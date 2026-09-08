import type { ReactNode } from "react";
import { AlertTriangle, Loader2, RefreshCw } from "lucide-react";
import { moeda as fmtMoeda, pct as fmtPct } from "./formato";

export type Tom = "alta" | "baixa" | "atencao" | "info" | "neutro";

const chip: Record<Tom, string> = {
  alta: "border-alta/40 bg-alta-2 text-alta",
  baixa: "border-baixa/40 bg-baixa-2 text-baixa",
  atencao: "border-atencao/40 bg-atencao-2 text-atencao",
  info: "border-info/40 bg-info-2 text-info",
  neutro: "border-borda-2 bg-painel-2 text-texto-2",
};

const cor: Record<Tom, string> = {
  alta: "text-alta",
  baixa: "text-baixa",
  atencao: "text-atencao",
  info: "text-info",
  neutro: "text-texto",
};

/** Chip quadrado, em mono: estado lido de relance. */
export function Etiqueta({ tom = "neutro", children, titulo }: { tom?: Tom; children: ReactNode; titulo?: string }) {
  return (
    <span
      title={titulo}
      className={`mono inline-flex items-center gap-1 whitespace-nowrap rounded-term border px-1.5 py-[1px] text-[10.5px] font-medium uppercase tracking-wider ${chip[tom]}`}
    >
      {children}
    </span>
  );
}

/** Painel chato de terminal: borda de 1px, cabecalho em faixa, sem sombra. */
export function Painel({
  titulo,
  meta,
  acoes,
  children,
  className = "",
  semRecheio = false,
}: {
  titulo?: ReactNode;
  meta?: ReactNode;
  acoes?: ReactNode;
  children: ReactNode;
  className?: string;
  semRecheio?: boolean;
}) {
  return (
    <section className={`rounded-term border border-borda bg-painel ${className}`}>
      {(titulo || meta || acoes) && (
        <header className="flex min-h-9 flex-wrap items-center justify-between gap-x-4 gap-y-1 border-b border-borda px-3 py-1.5">
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
            {titulo && <h2 className="eyebrow text-texto-2">{titulo}</h2>}
            {meta && <span className="mono text-[11px] text-texto-3">{meta}</span>}
          </div>
          {acoes && <div className="flex items-center gap-2">{acoes}</div>}
        </header>
      )}
      <div className={semRecheio ? "" : "p-3"}>{children}</div>
    </section>
  );
}

/** Ladrilho de numero: rotulo em eyebrow, valor grande em mono. */
export function Ladrilho({
  rotulo,
  valor,
  sub,
  tom = "neutro",
  extra,
}: {
  rotulo: string;
  valor: ReactNode;
  sub?: ReactNode;
  tom?: Tom;
  extra?: ReactNode;
}) {
  return (
    <div className="flex flex-col justify-between rounded-term border border-borda bg-painel px-3 py-2.5">
      <div className="flex items-center justify-between gap-2">
        <p className="eyebrow">{rotulo}</p>
        {extra}
      </div>
      <p className={`mono mt-1.5 text-[22px] font-medium leading-none ${cor[tom]}`}>{valor}</p>
      {sub && <p className="mono mt-1.5 text-[11px] text-texto-2">{sub}</p>}
    </div>
  );
}

/** Variacao com seta, sinal e cor, em mono. */
export function Delta({
  valor,
  tipo = "pct",
  casas = 1,
  moeda,
  className = "",
}: {
  valor: number | null | undefined;
  tipo?: "pct" | "moeda";
  casas?: number;
  moeda?: string;
  className?: string;
}) {
  if (valor === null || valor === undefined || Number.isNaN(valor)) return <span className={`mono text-texto-3 ${className}`}>—</span>;
  const tom = valor > 0 ? "text-alta" : valor < 0 ? "text-baixa" : "text-texto-2";
  const seta = valor > 0 ? "▲" : valor < 0 ? "▼" : "";
  const texto = tipo === "pct" ? fmtPct(valor, casas, true) : fmtMoeda(valor, moeda, true);
  return (
    <span className={`mono whitespace-nowrap ${tom} ${className}`}>
      {seta && <span className="mr-0.5 text-[9px] align-middle">{seta}</span>}
      {texto}
    </span>
  );
}

/** Mini-curva de banca: area suave, linha fina, ponto final. */
export function Sparkline({
  valores,
  base,
  largura = 96,
  altura = 24,
}: {
  valores: number[];
  base?: number;
  largura?: number;
  altura?: number;
}) {
  if (valores.length < 2) {
    return (
      <svg width={largura} height={altura} className="block">
        <line x1={0} x2={largura} y1={altura / 2} y2={altura / 2} stroke="var(--color-borda-2)" strokeDasharray="2 3" />
      </svg>
    );
  }
  const todos = base === undefined ? valores : [...valores, base];
  const min = Math.min(...todos);
  const max = Math.max(...todos);
  const faixa = max - min || 1;
  const x = (i: number) => 1 + (i / (valores.length - 1)) * (largura - 2);
  const y = (v: number) => altura - 2 - ((v - min) / faixa) * (altura - 4);
  const pontos = valores.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`);
  const ultimo = valores[valores.length - 1];
  const tinta = ultimo >= (base ?? valores[0]) ? "var(--color-alta)" : "var(--color-baixa)";
  const area = `M${x(0).toFixed(1)},${altura} L${pontos.join(" L")} L${x(valores.length - 1).toFixed(1)},${altura} Z`;
  return (
    <svg width={largura} height={altura} className="block" aria-hidden="true">
      {base !== undefined && (
        <line x1={0} x2={largura} y1={y(base).toFixed(1)} y2={y(base).toFixed(1)} stroke="var(--color-borda-2)" strokeDasharray="2 3" />
      )}
      <path d={area} fill={tinta} fillOpacity={0.14} />
      <polyline points={pontos.join(" ")} fill="none" stroke={tinta} strokeWidth={1.25} />
      <circle cx={x(valores.length - 1).toFixed(1)} cy={y(ultimo).toFixed(1)} r={1.8} fill={tinta} />
    </svg>
  );
}

export function Ponto({ tom, pulsar = false, titulo }: { tom: Tom; pulsar?: boolean; titulo?: string }) {
  const fundo = { alta: "bg-alta", baixa: "bg-baixa", atencao: "bg-atencao", info: "bg-info", neutro: "bg-texto-3" }[tom];
  return <span title={titulo} className={`inline-block h-1.5 w-1.5 rounded-full ${fundo} ${pulsar ? "pulsar" : ""}`} />;
}

/** Ticker como a corretora mostra: BTC forte, /USDT apagado. */
export function Par({ par, className = "" }: { par: string; className?: string }) {
  const [base, cotacao] = par.split("/");
  return (
    <span className={`mono whitespace-nowrap ${className}`}>
      <span className="font-medium text-texto">{base}</span>
      {cotacao && <span className="text-texto-3">/{cotacao}</span>}
    </span>
  );
}

export function Botao({
  children,
  onClick,
  ativo = false,
  pequeno = false,
  desabilitado = false,
  titulo,
}: {
  children: ReactNode;
  onClick?: () => void;
  ativo?: boolean;
  pequeno?: boolean;
  desabilitado?: boolean;
  titulo?: string;
}) {
  return (
    <button
      type="button"
      title={titulo}
      disabled={desabilitado}
      onClick={onClick}
      className={`mono inline-flex items-center gap-1.5 rounded-term border uppercase tracking-wider transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${
        pequeno ? "h-6 px-2 text-[10.5px]" : "h-7 px-2.5 text-[11px]"
      } ${ativo ? "border-acento/60 bg-info-2 text-acento" : "border-borda-2 bg-painel-2 text-texto-2 hover:border-texto-3 hover:text-texto"}`}
    >
      {children}
    </button>
  );
}

export function BotaoAtualizar({ onClick, carregando }: { onClick: () => void; carregando: boolean }) {
  return (
    <Botao onClick={onClick} pequeno desabilitado={carregando} titulo="Buscar velas novas e recalcular">
      <RefreshCw size={12} className={carregando ? "animate-spin" : ""} /> atualizar
    </Botao>
  );
}

export function Carregando({ texto = "Carregando…" }: { texto?: string }) {
  return (
    <div className="mono flex items-center gap-3 rounded-term border border-borda bg-painel px-4 py-6 text-[12px] text-texto-2">
      <Loader2 size={15} className="animate-spin text-acento" /> {texto}
    </div>
  );
}

export function Erro({ erro, tentar }: { erro: Error | string; tentar?: () => void }) {
  const mensagem = typeof erro === "string" ? erro : erro.message;
  return (
    <div className="flex flex-wrap items-center gap-3 rounded-term border border-baixa/40 border-l-2 border-l-baixa bg-baixa-2/50 px-4 py-3 text-[12px] text-baixa">
      <AlertTriangle size={15} />
      <span className="flex-1">{mensagem}</span>
      {tentar && (
        <Botao onClick={tentar} pequeno>
          tentar de novo
        </Botao>
      )}
    </div>
  );
}

/** Faixa de aviso com barra de severidade a esquerda. */
export function Aviso({ tom = "atencao", rotulo, titulo, children }: { tom?: Tom; rotulo?: string; titulo: ReactNode; children?: ReactNode }) {
  const barra = { alta: "border-l-alta", baixa: "border-l-baixa", atencao: "border-l-atencao", info: "border-l-info", neutro: "border-l-texto-3" }[tom];
  return (
    <div className={`rounded-term border border-borda border-l-2 bg-painel px-4 py-3 ${barra}`}>
      {rotulo && <p className={`eyebrow mb-1 ${cor[tom]}`}>{rotulo}</p>}
      <p className="text-[13px] font-medium text-texto">{titulo}</p>
      {children && <div className="mt-1 text-[12.5px] leading-relaxed text-texto-2">{children}</div>}
    </div>
  );
}

export function Vazio({ texto }: { texto: string }) {
  return <p className="mono rounded-term border border-dashed border-borda-2 px-4 py-5 text-center text-[11.5px] text-texto-3">{texto}</p>;
}

export const th = "px-2.5 py-1.5 text-left eyebrow whitespace-nowrap border-b border-borda";
export const thNum = `${th} text-right`;
export const td = "px-2.5 py-[7px] text-[12.5px] whitespace-nowrap align-middle";
export const tdNum = `${td} mono text-right`;

export function Tabela({ children }: { children: ReactNode }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse [&_tbody_tr]:border-b [&_tbody_tr]:border-borda/70 [&_tbody_tr:last-child]:border-b-0 [&_tbody_tr:hover]:bg-painel-2/70">
        {children}
      </table>
    </div>
  );
}

export function Campo({ rotulo, children }: { rotulo: string; children: ReactNode }) {
  return (
    <div>
      <p className="eyebrow">{rotulo}</p>
      <div className="mono mt-0.5 text-[13px]">{children}</div>
    </div>
  );
}
