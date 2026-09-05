"""Filtro de sinais por aprendizado de maquina - com o controle que o impede de mentir.

O modelo NAO decide quando operar. Quem decide e o setup. O modelo so aprende,
entre os sinais que o setup ja emitiu, quais costumam dar certo, e recusa os
outros. E o unico papel em que aprendizado de maquina funciona nessa escala de
dados: como operadora, com centenas de exemplos, ela decora ruido.

Tres protecoes embutidas, e nenhuma e opcional:

**Walk-forward com purga.** Cada janela treina so com trades que ja tinham
FECHADO antes do corte e testa nos que entraram depois. Um trade aberto antes
e fechado depois fica de fora do treino - seu rotulo ainda nao existia.

**Controle por embaralhamento.** O mesmo procedimento roda de novo com os
rotulos embaralhados. Se o filtro "melhora" tanto no controle quanto no real,
a melhora e artefato do procedimento, nao sinal. E a versao para ML do teste
de permutacao do backtest.

**Total, nao so media.** Um filtro que mantem 20% dos trades com expectancia
alta pode render MENOS que operar todos. O relatorio mostra R acumulado antes
e depois, nao so a media por trade.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score

from .conjunto import Conjunto, cortes_por_meses, dividir_por_tempo


@dataclass(frozen=True)
class ConfigFiltro:
    limiar: float = 0.5
    max_iter: int = 120
    max_depth: int = 3
    learning_rate: float = 0.05
    min_samples_leaf: int = 25
    l2: float = 1.0
    semente: int = 42


class FiltroML:
    """Aprende quais sinais de UM setup tendem a vencer."""

    def __init__(self, config: ConfigFiltro | None = None) -> None:
        self.config = config or ConfigFiltro()
        self.colunas: list[str] = []
        self.modelo: HistGradientBoostingClassifier | None = None
        self.taxa_base: float = float("nan")

    @property
    def treinado(self) -> bool:
        return self.modelo is not None

    def _matriz(self, entradas: pd.DataFrame) -> np.ndarray:
        numericas = entradas.select_dtypes(include=[np.number, "bool"])
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
        self.modelo = HistGradientBoostingClassifier(
            max_iter=c.max_iter,
            max_depth=c.max_depth,
            learning_rate=c.learning_rate,
            min_samples_leaf=c.min_samples_leaf,
            l2_regularization=c.l2,
            random_state=c.semente,
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
        joblib.dump({"config": self.config, "colunas": self.colunas,
                     "modelo": self.modelo, "taxa_base": self.taxa_base}, caminho)

    @classmethod
    def carregar(cls, caminho: str) -> "FiltroML":
        # joblib e pickle por baixo, e pickle executa codigo ao desserializar.
        # So carregue arquivos que ESTE projeto gravou com `salvar`, no seu
        # proprio disco. Um .pkl vindo de terceiro e um programa, nao um dado.
        dados = joblib.load(caminho)
        filtro = cls(dados["config"])
        filtro.colunas = dados["colunas"]
        filtro.modelo = dados["modelo"]
        filtro.taxa_base = dados["taxa_base"]
        return filtro


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


@dataclass
class RelatorioFiltro:
    janelas: list[Janela] = field(default_factory=list)
    controle: list[Janela] = field(default_factory=list)

    def _agregar(self, janelas: list[Janela]) -> dict:
        if not janelas:
            return {}
        n_teste = sum(j.n_teste for j in janelas)
        mantidos = sum(j.mantidos for j in janelas)
        return {
            "janelas": len(janelas),
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

    def veredito(self) -> str:
        r, c = self.real, self.embaralhado
        if not r:
            return "Sem janelas suficientes para avaliar."
        ganho_real = r["r_total_depois"] - r["r_total_antes"]
        ganho_controle = c.get("r_total_depois", 0) - c.get("r_total_antes", 0) if c else 0.0
        auc = r["auc_medio"]
        linhas = [
            f"AUC medio fora da amostra: {auc:.3f} (0,5 = moeda ao ar)",
            f"R acumulado sem filtro: {r['r_total_antes']:+.1f} | com filtro: "
            f"{r['r_total_depois']:+.1f} (mantendo {r['fracao_mantida']:.0%} dos trades)",
        ]
        if c:
            linhas.append(
                f"Controle com rotulos embaralhados: {c['r_total_antes']:+.1f} -> "
                f"{c['r_total_depois']:+.1f}"
            )
        if auc < 0.55:
            linhas.append("VEREDITO: o modelo nao distingue vencedor de perdedor. Nao usar.")
        elif ganho_real <= max(0.0, ganho_controle):
            linhas.append(
                "VEREDITO: a 'melhora' nao supera o controle embaralhado - e artefato "
                "do procedimento, nao sinal. Nao usar."
            )
        else:
            linhas.append(
                "VEREDITO: ha sinal acima do controle. Ainda assim, so o desempenho "
                "para frente confirma - trate como hipotese."
            )
        return "\n".join(linhas)


def _avaliar_janelas(conjunto: Conjunto, config: ConfigFiltro, cortes: list[pd.Timestamp]) -> list[Janela]:
    janelas = []
    for corte in cortes:
        treino, teste = dividir_por_tempo(conjunto, corte)
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
    return janelas


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
    relatorio = RelatorioFiltro(janelas=_avaliar_janelas(conjunto, config, cortes))

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
        relatorio.controle = _avaliar_janelas(embaralhado, config, cortes)
    return relatorio
