# Análise de cripto com backtest honesto

Motor de análise técnica e backtest para criptomoedas. Gera sinais de swing
(1h/4h) e — o mais importante — **mede quanto eles realmente acertam**, com as
correções estatísticas que fazem a diferença entre um número útil e um número
bonito.

**Não envia ordens.** O sistema analisa, mede e sugere tamanho de posição. Quem
opera é você.

---

## Por que o projeto mudou de corretora

A versão anterior era construída sobre a API da NovaDAX. A **NovaDAX encerrou as
operações no Brasil** (anúncio em 08/06/2026, desligamento até 30/09/2026) e o
host `api.novadax.com` saiu do ar — hoje ele nem resolve no DNS.

A lição virou arquitetura: a fonte de dados agora é o **CCXT**, que fala com mais
de cem corretoras pela mesma interface. Trocar de corretora é mudar uma linha do
`.env`, não reescrever o sistema.

```bash
EXCHANGE=binance    # ou bybit, okx, kraken, mexc, gateio...
```

---

## Começando

```bash
python -m venv .venv && .venv/Scripts/activate     # Windows
pip install -r requirements.txt
cp .env.example .env
```

Nenhuma chave de API é necessária — o sistema só usa endpoints públicos de
mercado.

```bash
python cli.py baixar   --par BTC/USDT --tf 1h --desde 2024-01-01
python cli.py backtest --par BTC/USDT --tf 4h --estrategia rsi_macd
python cli.py comparar --par BTC/USDT --tf 4h --correlacao
python cli.py validar  --par BTC/USDT --tf 4h --estrategia rsi_macd
python cli.py analisar --par BTC/USDT --tf 4h
```

`baixar` é separado de `backtest` de propósito: o backtest roda do cache, sem
rede. Assim o resultado é reproduzível e uma queda de conexão não encurta a
janela de dados em silêncio, mudando as métricas sem aviso.

Para acompanhar o mercado continuamente (só registra sinais, não opera):

```bash
python worker_service.py
```

---

## Como ler o relatório

O relatório coloca **expectância** e **fator de lucro** antes da taxa de acerto,
de propósito.

> **Taxa de acerto sozinha engana.** 90% de acerto com payoff 1:20 perde
> dinheiro; 35% com 1:4 é excelente. Pior: acerto é trivialmente maximizável —
> aproxime o alvo, afaste o stop, e você chega a 95% com uma única perda que
> apaga o ano. Por isso ele nunca é impresso sem payoff, intervalo de confiança
> e expectância ao lado.

| Métrica | O que responde |
|---|---|
| **Expectância (R)** | Quanto você ganha por unidade de risco, em média. É o número que decide. |
| **Fator de lucro** | Soma dos ganhos ÷ soma das perdas. Abaixo de 1, perde. |
| **Taxa de acerto + IC 95%** | Com 40 trades, 55% observado significa "entre 40% e 69%". O intervalo é a informação. |
| **Saídas ambíguas** | Quanto das saídas tocou stop e alvo na mesma vela. Acima de 25%, o backtest não consegue julgar a estratégia nesse timeframe. |

Abaixo de **30 trades** o relatório carimba `AMOSTRA INSUFICIENTE`. Para agir com
base no número, mire 100 ou mais.

---

## O que já foi medido

Rodando em BTC/USDT 4h desde 2024, com taxa de 0,1% por lado e slippage de 5 bps:

- A estratégia clássica **RSI + MACD não tem vantagem demonstrável**. Sensibilidade
  ao atraso: executar no fechamento da própria vela do sinal rende +16,8%;
  executar na abertura seguinte (o realizável) rende +0,6%; esperar mais uma vela
  vira −3,8%. Praticamente toda a "vantagem" morava em olhar um preço que ainda
  não existia.
- **O custo come o resto**: com taxa zero o retorno é +12,8%; com a taxa real,
  +0,6%; com o dobro, −10,4%.
- **Teste de permutação**: p = 0,106. Indistinguível do acaso.
- **Walk-forward**: expectância de +1,29 R dentro da amostra contra +0,80 R fora.
  Essa diferença é quanto da estratégia era decoreba de ruído.

Isso é o sistema funcionando, não falhando. O valor está em descobrir antes de
arriscar dinheiro, em vez de depois.

---

## Arquitetura

As camadas espelham o QuantConnect LEAN — Dados → Indicadores → Alpha → Risco —
porque é o que permite o **mesmo código de estratégia** rodar ao vivo e no
backtest. Sem isso, os dois divergem e a medição perde o sentido.

```
nucleo/
├── dados/          provedor CCXT, cache SQLite, agregação de timeframe
├── indicadores/    funções puras: RSI e ATR de Wilder, MACD, ADX, Donchian
├── estrategias/    contrato Estrategia -> sinais; rsi_macd, ema, composta
├── risco/          dimensionamento por risco por trade
└── backtest/       motor vela a vela, métricas, permutação, walk-forward
cli.py              entrypoints
worker_service.py   acompanhamento contínuo (só registra)
testes/             56 testes
```

### Decisões que sustentam o número

- **Execução na abertura da vela seguinte.** Decide no fechamento de `i`, executa
  em `abertura[i+1]`. O deslocamento acontece num único ponto do código, para que
  nenhuma estratégia nova possa esquecer dele.
- **Vela em formação é descartada** antes de qualquer cálculo — usá-la é
  look-ahead disfarçado.
- **Stop e alvo na mesma vela → assume stop.** O OHLC não diz qual veio primeiro;
  o erro cai para o lado seguro de propósito.
- **Vela que abre além do stop preenche na abertura**, não no stop. Assumir o
  contrário esconde exatamente as perdas de cauda.
- **Agregação 4h com `origin="epoch"`** e completude por contagem. Sem isso a
  grade se desloca conforme a data pedida, e o mesmo backtest dá números
  diferentes. O 4h derivado foi conferido contra o 4h nativo da Binance:
  divergência zero no OHLC.
- **Sinal como evento, não estado.** `rsi < 40 e macd > sinal` é verdadeiro por
  uma sequência de velas, não só na virada. Tratar estado como evento infla a
  contagem de trades em várias vezes e leva a conta de taxas junto.

### Correções em relação à versão anterior

| Era | Virou |
|---|---|
| `pd.DataFrame(registros, columns=[...])` com nomes que não batiam com o payload → quadro com o número certo de linhas e **tudo NaN**, sem erro | `validar_velas()` estoura na hora |
| RSI e ATR com média simples | Suavização de Wilder. Em 4.319 velas de BTC/USDT 1h, os dois discordam sobre cruzar 40 em **13,4% das barras** |
| Pedia 100 velas e calculava média de 200 → tendência sempre "LATERAL", filtro nunca filtrava | `barras_de_aquecimento()` declarado e honrado pelo carregador |
| Suporte/resistência incluindo a própria vela em análise | Canal de Donchian deslocado uma barra |
| Sem backtest | Motor, métricas, permutação e walk-forward |

---

## Fora de escopo

- **Envio de ordens.** Deliberado. Código capaz de operar dinheiro num projeto em
  iteração é passivo, não recurso.
- **Forex.** A NovaDAX não tinha, e a Binance também não. A interface
  `ProvedorDados` aceita uma fonte de forex depois sem tocar na análise.
- **Frontend.** O `front_example/` (Next.js) é mock e não se conecta a nada;
  ligá-lo exige uma API HTTP no backend.
- `database.py` e `alert_system.py` são legado da versão anterior e não estão no
  caminho de execução.

---

## Aviso

Backtest mede o passado. Nenhuma configuração de indicadores garante acerto no
futuro, e a maior parte das estratégias técnicas simples não sobrevive aos custos
de transação quando medida corretamente. Este projeto não é recomendação de
investimento.
