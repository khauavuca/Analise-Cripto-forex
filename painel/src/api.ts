// Cliente da API do painel. Os tipos espelham o que nucleo/painel.py devolve.

export type Setup = { nome: string; completo: string; descricao: string };

export type Estado = {
  versao: string;
  agora: string;
  fuso: string;
  rotulo_fuso: string;
  corretora: string;
  rede: boolean;
  pares: string[];
  timeframes: string[];
  setups: Setup[];
  campanha: {
    inicio: string;
    fim: string;
    banca: number;
    moeda: string;
    timeframes: string[];
    dias_restantes: number;
    encerrada: boolean;
  };
  velas: {
    par: string;
    timeframe: string;
    ultima: string | null;
    fechamento: number | null;
    variacao_24h: number | null;
    total: number;
  }[];
  nuvem: { gerado_em: string | null; ultima_vela: string | null } | null;
};

export type LinhaRanking = {
  trader: string;
  nome_curto: string;
  descricao: string;
  banca_inicial: number;
  banca_atual: number;
  resultado: number;
  variacao: number;
  operacoes: number;
  ganhas: number;
  perdidas: number;
  acerto: number | null;
  acerto_de: number | null;
  acerto_ate: number | null;
  payoff: number | null;
  acerto_para_empatar: number | null;
  media_por_operacao: number | null;
  em_aberto: number;
  maior_queda: number;
  recusadas: number;
};

export type Fechada = { momento: string; par: string; estrategia: string; valor: number; resultado: number; saldo: number };
export type Aberta = {
  par: string;
  timeframe: string;
  direcao: number;
  entrada: string;
  preco_entrada: number;
  stop: number;
  alvo: number;
};

export type Campanha = {
  fuso: string;
  gerado_em: string;
  inicio: string;
  fim: string;
  fim_inclusivo?: string;
  banca_inicial: number;
  moeda: string;
  pares: string[];
  timeframes: string[];
  ultima_vela: string | null;
  velas_no_periodo: number;
  ranking: LinhaRanking[];
  traders: Record<string, { banca_atual: number; fechadas: Fechada[]; abertas: Aberta[]; recusadas: Record<string, number> }>;
  curvas: Record<string, { t: string; saldo: number }[]>;
  relatorio: string;
  cedo: boolean;
  minimo_para_opinar: number;
  maximo_operacoes: number;
};

export type Recomendacao = {
  vela: string;
  par: string;
  timeframe: string;
  estrategia: string;
  nome_curto: string;
  direcao: number;
  lado: string;
  forca: number;
  preco: number;
  stop: number;
  alvo: number;
  motivo: string;
  decisao: "ENTRAR" | "RECUSADA";
  motivo_recusa: string;
  valor_ordem: number;
  risco_moeda: number;
  probabilidade: number | null;
  leituras: string[];
  stop_pct: number | null;
  alvo_pct: number | null;
  razao_risco_retorno: number | null;
};

export type Decisao = {
  gerado_em: string;
  banca: number;
  moeda: string;
  regras: {
    risco_por_trade: number;
    max_posicoes: number;
    max_por_par: number;
    exposicao_maxima: number;
    perda_diaria_maxima: number;
  };
  recomendacoes: Recomendacao[];
  entrar: number;
};

export type Vela = { t: number; o: number; h: number; l: number; c: number; v: number };
export type Sinal = {
  t: number;
  direcao: number;
  forca: number;
  stop: number | null;
  alvo: number | null;
  motivo: string;
  preco: number;
  quando: string;
};

export type Operacao = {
  setup: string;
  direcao: number;
  entrada: string;
  t_entrada: number;
  preco_entrada: number;
  saida: string;
  t_saida: number;
  preco_saida: number;
  motivo_saida: string;
  aberto: boolean;
  retorno_liquido_pct: number;
  multiplo_r: number;
  stop: number;
  alvo: number;
  barras: number;
};

export type Velas = {
  par: string;
  timeframe: string;
  setup: string;
  setup_completo: string;
  fuso: string;
  rotulo_fuso: string;
  ultima_vela: string;
  preco: number;
  velas: Vela[];
  linhas: Record<string, { t: number; v: number }[]>;
  sinais: Sinal[];
  ultimo_sinal: Sinal | null;
  ultimo_trade: Operacao | null;
  motivo_sem_trade: string | null;
  operacoes: Operacao[];
  // Presente so com setup=todos: cada setup com os proprios sinais e operacoes.
  por_setup: SetupNoGrafico[] | null;
};

export type SetupNoGrafico = {
  nome: string;
  completo: string;
  sinais: Sinal[];
  operacoes: Operacao[];
  ultimo_trade: Operacao | null;
  motivo_sem_trade: string | null;
};

export type ResumoSetup = {
  estrategia: string;
  nome_curto: string;
  fechados: number;
  abertos: number;
  ganhos: number;
  perdidos: number;
  acerto: number | null;
  acerto_de: number | null;
  acerto_ate: number | null;
  payoff: number | null;
  acerto_para_empatar: number | null;
  expectancia_r: number | null;
  retorno_medio: number | null;
  amostra_ok: boolean;
};

export type TradeRastreado = {
  estrategia: string;
  nome_curto: string;
  par: string;
  timeframe: string;
  direcao: number;
  entrada: string;
  saida: string | null;
  preco_entrada: number;
  preco_saida: number | null;
  stop: number;
  alvo: number;
  retorno_liquido_pct: number | null;
  multiplo_r: number | null;
  motivo_saida: string;
  barras_no_trade: number | null;
  aberto: boolean;
};

export type Rastreio = {
  gerado_em?: string;
  fontes: number;
  observacoes: number;
  sinais: number;
  fechados?: number;
  abertos?: number;
  minimo_para_concluir?: number;
  setups: ResumoSetup[];
  trades: TradeRastreado[];
  veredito: string;
};

async function pegar<T>(url: string): Promise<T> {
  const resposta = await fetch(url);
  if (!resposta.ok) {
    let mensagem = `${resposta.status} ${resposta.statusText}`;
    try {
      const corpo = await resposta.json();
      if (corpo?.detail) mensagem = String(corpo.detail);
    } catch {
      /* sem corpo */
    }
    throw new Error(mensagem);
  }
  return resposta.json() as Promise<T>;
}

export const api = {
  estado: () => pegar<Estado>("/api/estado"),
  campanha: (atualizar = false) => pegar<Campanha>(`/api/campanha?atualizar=${atualizar}`),
  decisao: (banca: number, moeda: string, atualizar = false) =>
    pegar<Decisao>(`/api/decisao?banca=${banca}&moeda=${encodeURIComponent(moeda)}&atualizar=${atualizar}`),
  velas: (par: string, tf: string, setup: string, barras = 300) =>
    pegar<Velas>(
      `/api/velas?par=${encodeURIComponent(par)}&tf=${tf}&setup=${encodeURIComponent(setup)}&barras=${barras}`,
    ),
  rastreio: (atualizar = false) => pegar<Rastreio>(`/api/rastreio?atualizar=${atualizar}`),
};
