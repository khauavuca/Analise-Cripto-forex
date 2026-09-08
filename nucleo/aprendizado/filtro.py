"""Filtro de sinais por aprendizado de maquina - com o controle que o impede de mentir.

O modelo NAO decide quando operar. Quem decide e o setup. O modelo so aprende,
entre os sinais que o setup ja emitiu, quais costumam dar certo, e recusa os
outros. E o unico papel em que aprendizado de maquina funciona nessa escala de
dados: como operadora, com centenas de exemplos, ela decora ruido.

O que o mantem respeitando o grafico em vez de achar padrao em tudo:

**Um modelo, todos os setups.** O setup entra como categoria. Cinco vezes mais
exemplos do que um modelo por setup, e ele aprende como cada setup se comporta
com cada leitura - em vez de oito modelos cegos uns para os outros.

**Monotonia.** Para as leituras universais (tendencia nas varias escalas e
concordancia entre setups), "a favor" nunca pode diminuir a probabilidade. O
modelo escolhe o peso, inclusive zero, mas nao pode inverter a logica do
grafico. Momento de curto prazo fica livre: setup de reversao entra contra ele.

**Sem hora nem dia da semana.** Foram as colunas "mais importantes" da primeira
versao. Eram ruido com nome.

**Walk-forward com purga.** Cada janela treina so com trades que ja tinham
FECHADO antes do corte e testa nos que entraram depois.

**Controle por embaralhamento.** O mesmo procedimento com os rotulos
embaralhados. Se o filtro "melhora" tanto no controle quanto no real, a melhora
e artefato do procedimento, nao sinal.

**Calibracao.** Quando o modelo diz 60%, quantos ganharam? Sem isso o numero na
tela e decoracao.

**Peneira.** Cada leitura, sozinha, fora da amostra: a favor contra oposto,
alto contra baixo. E o mapa do que funciona neste mercado, inclusive do que
nao funciona.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score

from .conjunto import Conjunto, cortes_por_meses, dividir_por_tempo
from .leituras import MONOTONAS


@dataclass(frozen=True)
class ConfigFiltro:
    limiar: float = 0.5
    max_iter: int = 120
    max_depth: int = 3
    learning_rate: float = 0.05
    min_samples_leaf: int = 25
    l2: float = 1.0
    semente: int = 42
    monotonico: bool = True
    categorica: str = "setup"
    excluir: tuple[str, ...] = ("hora", "dia_semana")


class FiltroML:
    """Aprende quais sinais tendem a vencer - de um setup ou de todos, com o setup como categoria."""

    def __init__(self, config: ConfigFiltro | None = None) -> None:
        self.config = config or ConfigFiltro()
        self.colunas: list[str] = []
        self.categorias: dict[str, int] = {}
        self.modelo: HistGradientBoostingClassifier | None = None
        self.taxa_base: float = float("nan")

    @property
    def treinado(self) -> bool:
        return self.modelo is not None

    def _matriz(self, entradas: pd.DataFrame) -> np.ndarray:
        e = entradas.drop(columns=[c for c in self.config.excluir if c in entradas.columns])
        categorica = self.config.categorica
        if categorica in e.columns:
            e = e.copy()
            if not self.colunas:
                # Codigos travados no treino, como as colunas. Categoria nova na
                # inferencia vira NaN, que o modelo trata como "desconhecida".
                valores = sorted(str(v) for v in e[categorica].dropna().unique())
                self.categorias = {v: i for i, v in enumerate(valores)}
            e[categorica] = e[categorica].map(lambda v: self.categorias.get(str(v), np.nan)).astype(float)
        numericas = e.select_dtypes(include=[np.number, "bool"])
        if not self.colunas:
            self.colunas = list(numericas.columns)
        # A ordem das colunas fica travada no treino. Reindexar garante que a
        # inferencia ve as mesmas colunas na mesma posicao, mesmo que o painel
        # ganhe ou perca uma coluna no futuro.
        return numericas.reindex(columns=self.colunas).to_numpy(dtype=float)

    def treinar(self, conjunto: Conjunto) -> "FiltroML":
        if len(conjunto) < 2 or conjunto.rotulos["venceu"].nunique() < 2:
            raise ValueError("Conjunto sem exemplos suficientes das duas classes.")
        x = self._matriz(conjunto.entradas)
        y = conjunto.rotulos["venceu"].to_numpy(dtype=int)
        c = self.config
        categoricas = [i for i, nome in enumerate(self.colunas) if nome == c.categorica]
        monotonia = [1 if nome in MONOTONAS else 0 for nome in self.colunas] if c.monotonico else None
        self.modelo = HistGradientBoostingClassifier(
            max_iter=c.max_iter,
            max_depth=c.max_depth,
            learning_rate=c.learning_rate,
            min_samples_leaf=c.min_samples_leaf,
            l2_regularization=c.l2,
            random_state=c.semente,
            monotonic_cst=monotonia,
            categorical_features=categoricas or None,
        )
        self.modelo.fit(x, y)
        self.taxa_base = float(y.mean())
        return self

    def probabilidade(self, entradas: pd.DataFrame) -> np.ndarray:
        if not self.treinado:
            raise RuntimeError("Filtro ainda nao treinado.")
        return self.modelo.predict_proba(self._matriz(entradas))[:, 1]

    def manter(self, entradas: pd.DataFrame) -> np.ndarray:
        return self.probabilidade(entradas) >= self.config.limiar

    def importancias(self, conjunto: Conjunto, repeticoes: int = 5) -> pd.Series:
        """Importancia por permutacao: quanto o AUC cai ao embaralhar cada coluna."""
        from sklearn.inspection import permutation_importance

        x = self._matriz(conjunto.entradas)
        y = conjunto.rotulos["venceu"].to_numpy(dtype=int)
        r = permutation_importance(
            self.modelo, x, y, n_repeats=repeticoes, random_state=self.config.semente,
            scoring="roc_auc",
        )
        return pd.Series(r.importances_mean, index=self.colunas).sort_values(ascending=False)

    def salvar(self, caminho: str) -> None:
        joblib.dump(
            {
                "config": self.config, "colunas": self.colunas, "categorias": self.categorias,
                "modelo": self.modelo, "taxa_base": self.taxa_base,
            },
            caminho,
        )

    @classmethod
    def carregar(cls, caminho: str) -> "FiltroML":
        # joblib e pickle por baixo, e pickle executa codigo ao desserializar.
        # So carregue arquivos que ESTE projeto gravou com `salvar`, no seu
        # proprio disco. Um .pkl vindo de terceiro e um programa, nao um dado.
        dados = joblib.load(caminho)
        filtro = cls(dados["config"])
        filtro.colunas = dados["colunas"]
        filtro.categorias = dados.get("categorias", {})
        filtro.modelo = dados["modelo"]
        filtro.taxa_base = dados["taxa_base"]
        return filtro


# ------------------------------------------------------------------ avaliacao


@dataclass
class Janela:
    corte: pd.Timestamp
    n_treino: int
    n_teste: int
    mantidos: int
    auc: float
    expect_antes: float
    expect_depois: float
    r_total_antes: float
    r_total_depois: float
    acerto_antes: float
    acerto_depois: float

    @property
    def melhorou(self) -> bool:
        """O filtro so vale a pena numa janela se o R acumulado subiu com ele."""
        return self.r_total_depois > self.r_total_antes

    @property
    def excesso(self) -> float:
        """Quanto o filtro rendeu ALEM de uma selecao aleatoria do mesmo tamanho.

        Cortar 80% de um conjunto perdedor melhora o total sozinho, sem saber
        nada. A seleccao aleatoria que mantem a mesma fracao renderia
        `fracao * r_total_antes`; o que passa disso e o que o modelo sabe.
        """
        if not self.n_teste:
            return 0.0
        return self.r_total_depois - (self.mantidos / self.n_teste) * self.r_total_antes


def calibracao(prob: np.ndarray, venceu: np.ndarray, faixas: int = 5) -> pd.DataFrame:
    """Probabilidade prevista contra acerto real, por faixa de previsao.

    Um modelo calibrado tem as duas colunas parecidas em toda faixa. Se ele diz
    70% e 45% ganham, o numero e decoracao - mesmo com AUC bom.
    """
    if len(prob) == 0:
        return pd.DataFrame(columns=["faixa", "n", "prob_media", "acerto_real"])
    quadro = pd.DataFrame({"prob": prob, "venceu": venceu})
    try:
        quadro["faixa"] = pd.qcut(quadro["prob"], faixas, duplicates="drop")
    except ValueError:
        quadro["faixa"] = "todas"
    saida = (
        quadro.groupby("faixa", observed=True)
        .agg(n=("venceu", "size"), prob_media=("prob", "mean"), acerto_real=("venceu", "mean"))
        .reset_index()
    )
    saida["faixa"] = saida["faixa"].astype(str)
    return saida


@dataclass
class RelatorioFiltro:
    janelas: list[Janela] = field(default_factory=list)
    controle: list[Janela] = field(default_factory=list)
    pontuacoes: list[tuple[np.ndarray, np.ndarray]] = field(default_factory=list)

    def _agregar(self, janelas: list[Janela]) -> dict:
        if not janelas:
            return {}
        n_teste = sum(j.n_teste for j in janelas)
        mantidos = sum(j.mantidos for j in janelas)
        return {
            "janelas": len(janelas),
            "melhoraram": sum(1 for j in janelas if j.melhorou),
            "acima_do_acaso": sum(1 for j in janelas if j.excesso > 0),
            "excesso": sum(j.excesso for j in janelas),
            "n_teste": n_teste,
            "mantidos": mantidos,
            "fracao_mantida": mantidos / n_teste if n_teste else float("nan"),
            "auc_medio": float(np.nanmean([j.auc for j in janelas])),
            "r_total_antes": sum(j.r_total_antes for j in janelas),
            "r_total_depois": sum(j.r_total_depois for j in janelas),
            "expect_antes": float(np.nansum([j.expect_antes * j.n_teste for j in janelas]) / n_teste) if n_teste else float("nan"),
            "expect_depois": float(np.nansum([j.expect_depois * j.mantidos for j in janelas]) / mantidos) if mantidos else float("nan"),
        }

    @property
    def real(self) -> dict:
        return self._agregar(self.janelas)

    @property
    def embaralhado(self) -> dict:
        return self._agregar(self.controle)

    def calibracao(self, faixas: int = 5) -> pd.DataFrame:
        if not self.pontuacoes:
            return calibracao(np.array([]), np.array([]), faixas)
        prob = np.concatenate([p for p, _ in self.pontuacoes])
        y = np.concatenate([v for _, v in self.pontuacoes])
        return calibracao(prob, y, faixas)

    def veredito(self) -> str:
        r, c = self.real, self.embaralhado
        if not r:
            return "Sem janelas suficientes para avaliar."
        excesso_real = r["excesso"]
        excesso_controle = c.get("excesso", 0.0) if c else 0.0
        auc = r["auc_medio"]
        linhas = [
            f"AUC medio fora da amostra: {auc:.3f} (0,5 = moeda ao ar)",
            f"R acumulado sem filtro: {r['r_total_antes']:+.1f} | com filtro: "
            f"{r['r_total_depois']:+.1f} (mantendo {r['fracao_mantida']:.0%} dos trades)",
            f"Expectancia por trade: {r['expect_antes']:+.3f} R sem filtro | "
            f"{r['expect_depois']:+.3f} R com filtro",
            f"Acima de uma selecao aleatoria do mesmo tamanho: {excesso_real:+.1f} R"
            + (f" | controle embaralhado: {excesso_controle:+.1f} R" if c else ""),
            f"Janelas em que o filtro melhorou o total: {r['melhoraram']} de {r['janelas']} "
            f"(acima do acaso em {r['acima_do_acaso']} de {r['janelas']})",
        ]
        if c:
            linhas.append(
                f"Controle com rotulos embaralhados: {c['r_total_antes']:+.1f} -> "
                f"{c['r_total_depois']:+.1f}"
            )
        consistente = r["acima_do_acaso"] >= max(2, int(np.ceil(0.6 * r["janelas"])))
        if auc < 0.55:
            linhas.append("VEREDITO: o modelo nao distingue vencedor de perdedor. Nao usar.")
        elif excesso_real <= 0 or excesso_real <= 2 * max(0.0, excesso_controle):
            linhas.append(
                "VEREDITO: o que o filtro rende alem de uma selecao aleatoria nao supera "
                "o dobro do que o controle embaralhado rende - e artefato do procedimento, "
                "nao sinal. Nao usar."
            )
        elif not consistente:
            linhas.append(
                "VEREDITO: ha sinal na soma, mas ele vem de poucas janelas. Inconsistente "
                "entre regimes - nao usar ainda."
            )
        else:
            linhas.append(
                "VEREDITO: sinal acima do controle e consistente entre janelas. Ainda assim, "
                "so o desempenho para frente confirma - trate como hipotese."
            )
        return "\n".join(linhas)


def _avaliar_janelas(
    conjunto: Conjunto, config: ConfigFiltro, cortes: list[pd.Timestamp]
) -> tuple[list[Janela], list[tuple[np.ndarray, np.ndarray]]]:
    janelas, pontuacoes = [], []
    for i, corte in enumerate(cortes):
        treino, teste = dividir_por_tempo(conjunto, corte)
        # Janelas de teste SEM sobreposicao: cada uma vai so ate o corte
        # seguinte. Testar "ate o fim" a partir de cada corte contaria os
        # mesmos trades varias vezes e faria seis janelas parecerem seis
        # confirmacoes independentes quando sao uma so.
        if i + 1 < len(cortes):
            dentro = (pd.to_datetime(teste.meta["entrada"], utc=True) < cortes[i + 1]).to_numpy()
            teste = Conjunto(
                teste.entradas[dentro].reset_index(drop=True),
                teste.rotulos[dentro].reset_index(drop=True),
                teste.meta[dentro].reset_index(drop=True),
            )
        if len(treino) < 30 or len(teste) < 10 or treino.rotulos["venceu"].nunique() < 2:
            continue
        filtro = FiltroML(config).treinar(treino)
        prob = filtro.probabilidade(teste.entradas)
        manter = prob >= config.limiar
        y = teste.rotulos["venceu"].to_numpy(dtype=int)
        r = teste.rotulos["multiplo_r"].to_numpy(dtype=float)
        auc = float(roc_auc_score(y, prob)) if len(np.unique(y)) > 1 else float("nan")
        janelas.append(
            Janela(
                corte=corte,
                n_treino=len(treino),
                n_teste=len(teste),
                mantidos=int(manter.sum()),
                auc=auc,
                expect_antes=float(np.nanmean(r)) if len(r) else float("nan"),
                expect_depois=float(np.nanmean(r[manter])) if manter.any() else float("nan"),
                r_total_antes=float(np.nansum(r)),
                r_total_depois=float(np.nansum(r[manter])),
                acerto_antes=float(y.mean()),
                acerto_depois=float(y[manter].mean()) if manter.any() else float("nan"),
            )
        )
        pontuacoes.append((prob, y))
    return janelas, pontuacoes


def avaliar_walkforward(
    conjunto: Conjunto,
    config: ConfigFiltro | None = None,
    meses_teste: int = 3,
    minimo_treino: int = 60,
    com_controle: bool = True,
    semente: int = 7,
) -> RelatorioFiltro:
    """Treina em cada janela e mede na seguinte; repete com rotulos embaralhados."""
    config = config or ConfigFiltro()
    conjunto = conjunto.ordenar_por_tempo()
    cortes = cortes_por_meses(conjunto, meses_teste, minimo_treino)
    janelas, pontuacoes = _avaliar_janelas(conjunto, config, cortes)
    relatorio = RelatorioFiltro(janelas=janelas, pontuacoes=pontuacoes)

    if com_controle and cortes:
        gerador = np.random.default_rng(semente)
        ordem = gerador.permutation(len(conjunto))
        # Embaralha os rotulos mantendo entradas e metadados no lugar: o modelo
        # ve as mesmas colunas, so que agora elas nao tem relacao com o desfecho.
        embaralhado = Conjunto(
            conjunto.entradas,
            conjunto.rotulos.iloc[ordem].reset_index(drop=True),
            conjunto.meta,
        )
        relatorio.controle, _ = _avaliar_janelas(embaralhado, config, cortes)
    return relatorio


def peneira(conjunto: Conjunto, a_partir_de: pd.Timestamp | None = None, minimo: int = 30) -> pd.DataFrame:
    """Cada leitura sozinha: R medio e acerto quando ela esta a favor contra quando esta contra.

    Para leituras sem direcao (volatilidade, volume, localizacao), compara o
    terco mais alto com o terco mais baixo. `a_partir_de` restringe as linhas
    ao periodo fora da amostra do walk-forward, para a peneira nao contar o
    que o modelo ja viu.
    """
    entradas, rotulos = conjunto.entradas, conjunto.rotulos
    if a_partir_de is not None and not conjunto.vazio:
        mascara = (pd.to_datetime(conjunto.meta["entrada"], utc=True) >= a_partir_de).to_numpy()
        entradas, rotulos = entradas[mascara], rotulos[mascara]

    linhas = []
    for coluna in entradas.columns:
        if coluna in ("setup", "hora", "dia_semana") or not pd.api.types.is_numeric_dtype(entradas[coluna]):
            continue
        valores = entradas[coluna].astype(float)
        ok = valores.notna()
        if ok.sum() < 2 * minimo:
            continue
        v = valores[ok]
        r = rotulos["multiplo_r"][ok].astype(float)
        y = rotulos["venceu"][ok].astype(float)
        distintos = set(v.unique())
        if coluna.endswith("_a_favor") or distintos <= {-1.0, 0.0, 1.0}:
            baixo, alto, tipo = v < 0, v > 0, "contra -> a favor"
        else:
            q1, q3 = v.quantile([1 / 3, 2 / 3])
            if q1 == q3:
                continue
            baixo, alto, tipo = v <= q1, v >= q3, "baixo -> alto"
        if baixo.sum() < minimo or alto.sum() < minimo:
            continue
        linhas.append(
            {
                "leitura": coluna, "comparacao": tipo,
                "n_baixo": int(baixo.sum()), "acerto_baixo": float(y[baixo].mean()), "r_baixo": float(r[baixo].mean()),
                "n_alto": int(alto.sum()), "acerto_alto": float(y[alto].mean()), "r_alto": float(r[alto].mean()),
            }
        )
    quadro = pd.DataFrame(linhas)
    if quadro.empty:
        return quadro
    quadro["delta_r"] = quadro["r_alto"] - quadro["r_baixo"]
    return quadro.reindex(quadro["delta_r"].abs().sort_values(ascending=False).index).reset_index(drop=True)
