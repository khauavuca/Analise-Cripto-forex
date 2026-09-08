// Formatacao em portugues do Brasil, num lugar so.

const moedas = new Map<string, Intl.NumberFormat>();

export function moeda(valor: number | null | undefined, codigo = "BRL", sinal = false): string {
  if (valor === null || valor === undefined || Number.isNaN(valor)) return "—";
  let f = moedas.get(codigo);
  if (!f) {
    try {
      f = new Intl.NumberFormat("pt-BR", { style: "currency", currency: codigo, maximumFractionDigits: 2 });
    } catch {
      f = new Intl.NumberFormat("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }
    moedas.set(codigo, f);
  }
  const texto = f.format(Math.abs(valor));
  if (!sinal) return valor < 0 ? `-${texto}` : texto;
  return valor < 0 ? `-${texto}` : `+${texto}`;
}

export function pct(valor: number | null | undefined, casas = 0, sinal = false): string {
  if (valor === null || valor === undefined || Number.isNaN(valor)) return "—";
  const texto = new Intl.NumberFormat("pt-BR", {
    style: "percent",
    minimumFractionDigits: casas,
    maximumFractionDigits: casas,
  }).format(Math.abs(valor));
  if (!sinal) return valor < 0 ? `-${texto}` : texto;
  return valor < 0 ? `-${texto}` : `+${texto}`;
}

export function numero(valor: number | null | undefined, casas = 2): string {
  if (valor === null || valor === undefined || Number.isNaN(valor)) return "—";
  return new Intl.NumberFormat("pt-BR", { minimumFractionDigits: casas, maximumFractionDigits: casas }).format(valor);
}

export function preco(valor: number | null | undefined): string {
  if (valor === null || valor === undefined || Number.isNaN(valor)) return "—";
  const casas = valor >= 1000 ? 2 : valor >= 10 ? 3 : 4;
  return numero(valor, casas);
}

// Le o deslocamento (-03:00) do proprio texto ISO e o transforma num fuso
// fixo que o Intl aceita. Sem deslocamento no texto, assume UTC.
export function fusoDe(iso: string): string {
  const m = iso.match(/([+-])(\d{2}):(\d{2})$/);
  if (!m) return "UTC";
  const horas = Number(m[2]);
  const minutos = Number(m[3]);
  if (horas === 0 && minutos === 0) return "UTC";
  // Etc/GMT tem o sinal invertido por convencao antiga.
  const sinal = m[1] === "-" ? "+" : "-";
  return minutos === 0 ? `Etc/GMT${sinal}${horas}` : "UTC";
}

function formatar(iso: string | null | undefined, opcoes: Intl.DateTimeFormatOptions): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  // O servidor ja manda o horario no fuso de quem le (com o deslocamento no
  // texto). Formatamos no mesmo fuso para nao "corrigir" duas vezes.
  return new Intl.DateTimeFormat("pt-BR", { ...opcoes, timeZone: fusoDe(iso) }).format(d);
}

export function dataHora(iso: string | null | undefined): string {
  return formatar(iso, { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

export function data(iso: string | null | undefined): string {
  return formatar(iso, { day: "2-digit", month: "2-digit", year: "numeric" });
}

export function hora(iso: string | null | undefined): string {
  return formatar(iso, { hour: "2-digit", minute: "2-digit" });
}

export function lado(direcao: number): string {
  return direcao > 0 ? "COMPRA" : direcao < 0 ? "VENDA" : "—";
}

export function classeSinal(valor: number | null | undefined): string {
  if (valor === null || valor === undefined || valor === 0) return "text-texto-2";
  return valor > 0 ? "text-alta" : "text-baixa";
}
