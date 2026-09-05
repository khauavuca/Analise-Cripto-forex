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

Todos os comandos rodam **de dentro desta pasta**, com o venv ativado.

No PowerShell (Windows):

```powershell
cd "C:\Users\USER\Desktop\Analise-Cripto-Forex\Analise-Cripto-forex"
.\.venv\Scripts\Activate.ps1
```

Primeira vez, para criar o ambiente:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

Se o `Activate.ps1` for bloqueado, libere só para esta sessão do terminal:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
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

10 pares em 4h desde 2022 (~4,8 anos), taxa de 0,1% por lado e slippage de 5 bps.
Os cinco primeiros foram usados durante o desenvolvimento; os cinco últimos só
foram tocados depois que as estratégias já estavam congeladas.

| Conjunto | Pares | Trades | Acerto | Payoff | Expectância | Fator de lucro |
|---|---|---|---|---|---|---|
| Desenvolvimento | BTC, ETH, SOL, BNB, XRP | 411 | 45,0% | 1,33 | +0,099 R | **1,06** |
| Nunca olhados | ADA, LINK, AVAX, DOT, LTC | 394 | 40,1% | 1,15 | −0,027 R | **0,77** |

A vantagem aparente no primeiro conjunto **não sobreviveu** no segundo. Nenhum
dos pares novos chega a significância (melhor p = 0,229) e todos têm fator de
lucro abaixo de 1.

O caso mais instrutivo é o ETH. Nele a confluência passou em tudo: permutação
com p = 0,005, vantagem estável sob atraso (+31% executando 1, 2 ou 3 velas
depois do sinal), sobrevivendo até ao triplo do custo, e walk-forward com
**+0,207 R fora da amostra contra +0,164 dentro**. Parecia sólido — e era
seleção. Escolher o melhor entre cinco pares testados produz exatamente esse
resultado, e só ficou visível quando as mesmas regras rodaram em pares que
ninguém tinha olhado.

A leitura honesta: RSI+MACD e cruzamento de médias, do jeito clássico, ficam
entre empatar e perder depois de custos em cripto líquida. A entrega deste
projeto não são essas duas estratégias — é o aparato que mede qualquer ideia
antes de ela custar dinheiro.

### Os seis setups profissionais

Cada um usa uma mecânica de decisão diferente das outras — não são variações do
mesmo oscilador. A correlação entre os sinais confirma: nenhum par passa de 0,41
em módulo (o mais alto é `donchian` × `vwap`, −0,41, que são opostos por
construção).

| Setup | Mecânica | Origem |
|---|---|---|
| `donchian` | rompe a máxima de 20 barras, stop 2×ATR | Tartarugas / CTAs |
| `momento` | sinal do retorno de 60 barras, normalizado por volatilidade | TSMOM (Moskowitz, Ooi & Pedersen) |
| `compressao` | Bollinger dentro de Keltner, entra quando solta | Squeeze de John Carter |
| `reversao_bb` | extremo de banda, só com ADX < 20 | reversão à média clássica |
| `vwap` | desvio do VWAP ancorado na semana | benchmark institucional de execução |
| `estrutura` | rompe topo confirmado, com topos e fundos ascendentes | price action / market structure |

Medidos em 4h desde 2022, ordenados pelo **fator de lucro nos 5 pares nunca
olhados** — a única coluna que não sofre de seleção:

| Setup | Trades (novos) | Fator (dev) | **Fator (novos)** | Acerto | Payoff |
|---|---|---|---|---|---|
| `ema` | 1058 | 0,98 | **1,16** | 48% | 1,27 |
| `donchian` | 1626 | 1,01 | 1,00 | 39% | 1,58 |
| `momento` | 1016 | 0,93 | 0,98 | 42% | 1,37 |
| `compressao` | 1122 | 0,94 | 0,95 | 36% | 1,68 |
| `rsi_macd` | 237 | 0,94 | 0,85 | 28% | 2,20 |
| `confluencia` | 394 | 1,06 | 0,77 | 40% | 1,15 |
| `estrutura` | 865 | 0,89 | **0,77** | **53%** | 0,69 |
| `vwap` | 2049 | 0,63 | 0,65 | 21% | 2,47 |
| `reversao_bb` | 1024 | 0,63 | 0,63 | 26% | 1,78 |

Três leituras:

**`estrutura` é a demonstração do problema da assertividade.** Tem a maior taxa
de acerto de todos — 53% nos pares novos — e é um dos que mais perde dinheiro,
porque o payoff é 0,69: os perdedores são maiores que os ganhadores. Quem
escolhesse setup por taxa de acerto escolheria justamente ele.

**A família de reversão perde de forma consistente** (`vwap` 0,63 e 0,65, `reversao_bb`
0,63 e 0,63 — praticamente idênticos nos dois conjuntos). Em cripto, que passa a maior parte do tempo
em tendência, comprar o extremo é ser atropelado. É um resultado estrutural, não
ruído.

**`ema` é o único candidato real.** Foi o único com fator acima de 1 fora da
amostra — e melhorou de 0,98 para 1,16 ao sair dela. Nos pares individuais:
ADA, AVAX e DOT dão p ≤ 0,007 com o retorno praticamente inalterado quando se
atrasa a execução em 1, 2 ou 3 velas (a assinatura de efeito real) e sobrevivem
ao dobro do custo; LINK e LTC perdem. É o comportamento esperado de um sistema
seguidor de tendência: ele ganha onde houve tendência, e é por isso que CTAs
rodam cestas de mercados em vez de um só.

### Calibragem: o que o MFE/MAE mostrou

`python cli.py calibrar` converte a excursão de cada trade em múltiplos de risco.
Nos mesmos 805 trades:

- O **stop não** está apertado: só 21% dos vencedores chegaram a passar de 0,7R
  contra antes de virar.
- O **alvo não** está longe: os vencedores capturam 81% da excursão favorável.
- **45% dos perdedores estiveram acima de +0,5R antes de morrer** (mediana
  +0,44R). Parecia o vazamento óbvio.

A hipótese natural — mover o stop para o empate depois de um lucro mínimo — foi
implementada e medida. **Piora:**

| Gatilho | Acerto (5 pares dev) | Fator de lucro | Fator nos 5 pares novos |
|---|---|---|---|
| desligado | 45,0% | **1,06** | 0,77 |
| 0,3R | 19,3% | 0,90 | 0,80 |
| 0,5R | 26,0% | 0,93 | 0,73 |
| 1,0R | 37,0% | 0,99 | 0,78 |

O motivo aparece na taxa de acerto: os vencedores **retraem pela mesma faixa** em
que os perdedores viram. No instante do gatilho não há como separar uns dos
outros, e mover o stop mata os dois. Fica desligado por padrão.

Vale como exemplo do que o projeto faz: uma melhoria plausível, com dado
aparentemente sustentando, medida em dez pares — e reprovada nos dois conjuntos.

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

### A estrutura para a IA de apoio

Quatro peças de backend, cada uma sustentando a seguinte. A IA, quando vier,
ocupa o lugar do filtro — o resto já existe e já está medido.

| Peça | Comando | O que faz |
|---|---|---|
| **Conjunto de treino** | `cli.py conjunto` | Junta o que o sistema via na barra do sinal com o que aconteceu depois. Níveis de preço viram distância relativa; a barra é a do **sinal**, não a da entrada. |
| **Filtro de ML** | `cli.py filtro` | Aprende, entre os sinais de um setup, quais tendem a vencer. Treinado por walk-forward com purga e comparado a um controle de rótulos embaralhados. |
| **Carteira** | `cli.py carteira` | Banca compartilhada com posições simultâneas, teto de exposição, perda diária máxima e pausa após sequência de perdas. |
| **Decisão** | `cli.py decidir` | Varre pares × setups, aplica as regras da carteira (e o filtro, se aprovado) e emite recomendação estruturada — tabela ou JSON. Nenhuma ordem é enviada. |

O que cada peça mostrou ao ser medida:

- **O filtro é honesto sobre si mesmo.** Em 10 pares desde 2022 (OKX): `ema`
  0,518 e `compressao` 0,510 de AUC fora da amostra — moeda ao ar, veredito
  *não usar*. `confluencia` tem o maior AUC (0,580) e mesmo assim é reprovado,
  porque o R acumulado cai e o controle embaralhado cai igual — ranquear bem
  não é a mesma coisa que ganhar dinheiro. Só o `donchian` (0,556, R de +468
  para +594 mantendo 29% dos trades) fica acima do controle: hipótese, não
  resultado.
- **As regras da carteira trocam retorno por sobrevivência.** Os três setups
  positivos em 90 dias, 10 pares: com regras, +50,2% com pior momento de −23,6%;
  **sem regras, +72,2% com pior momento de −53,2%**. Metade da queda por um
  terço do retorno — é a troca que um profissional faz sem pensar, porque a
  segunda queda de 53% é a que tira alguém do jogo. E juntar os nove setups na
  mesma banca quase zera o resultado: os perdedores ocupam as vagas dos
  ganhadores.
- **Vela a vela é igual a de uma vez.** `testes/test_replay.py` entrega a série
  uma vela por vez e compara: sinais e dinheiro batem. O motor não espia o
  futuro, e isso deixa de ser afirmação para ser teste.

Fluxo típico, uma vez por setup:

```bash
python cli.py conjunto --estrategia donchian --desde 2022-01-01
python cli.py filtro --conjunto dados/conjuntos/donchian_4h.csv --setup donchian
python cli.py decidir --estrategias donchian,confluencia --filtros modelos
```

`--setup` grava o modelo em `modelos/` com o nome que o `decidir` procura
depois (o nome do setup com parâmetros, ajustado para ser nome de arquivo
válido). Se preferir escolher o caminho, use `--salvar`.

### A campanha de teste

`python cli.py campanha` transforma cada setup num **trader com a própria
banca** — dinheiro de mentira, mercado de verdade — e mostra, em português
claro, quem está ganhando:

```bash
python cli.py campanha --inicio 2026-09-05 --fim 2026-09-11 --banca 500 --moeda BRL --tfs 1h,4h --salvar-em dados/campanha
```

Ela roda na nuvem junto com a coleta e o relatório fica em
`dados/campanha/relatorio.md`, atualizado a cada execução.

As datas de `--inicio`/`--fim` e todos os horários do relatório são no
**horário de Brasília** (`FUSO_HORARIO`, padrão `America/Sao_Paulo`). Por
dentro tudo continua em UTC, porque as velas da corretora são UTC e a nuvem e
a sua máquina precisam chegar ao mesmo resultado; a conversão acontece num
lugar só, `nucleo/tempo.py`.

Dois detalhes de desenho que valem entender:

- **Ela é refeita do zero a cada execução**, a partir das velas reais entre o
  início e agora. Não há estado guardado para se corromper: se o processo cair,
  a próxima execução chega ao mesmo resultado. E como vela a vela dá o mesmo que
  de uma vez (`test_replay.py`), a campanha é idêntica ao que o motor de backtest
  faria — por construção, não por promessa.
- **Só conta o que nasceu dentro dela.** As velas anteriores ao início entram
  apenas para aquecer indicadores. Os setups foram congelados antes, e é isso
  que faz dela um teste *para frente*: ninguém escolheu o período depois de ver
  o resultado.

Cada trader vem com três números que só fazem sentido juntos: **acerto** (com
a faixa em que o acerto verdadeiro provavelmente está), **payoff** (ganho médio
÷ perda média) e **quanto precisa acertar para empatar** com aquele payoff,
que é `1 / (1 + payoff)`. É a leitura que o acerto sozinho não dá: 50% de
acerto com payoff 1,40 precisa de 42% e está ganhando; 18% com payoff 0,35
precisaria de 74% e está perdendo — e os dois podem aparecer na mesma tabela
com "metade das operações ganhas".

O relatório avisa sozinho quando é cedo: abaixo de 30 operações fechadas, a
ordem dos traders é sorte, não habilidade. Uma semana no 4h raramente passa
disso — a campanha é o sistema inteiro com contabilidade de verdade, não um
veredito.

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
- **Stop e alvo valem já na barra de entrada.** Entra-se na abertura; o resto
  da barra acontece depois, e pode bater no stop. A primeira versão do motor
  pulava essa checagem e garantia que todo trade sobrevivesse à primeira vela —
  um trader de teste com stop de 0,1% "ganhou" 123% num mercado subindo. Os
  números deste documento foram re-medidos depois da correção.
- **O dia vira à meia-noite de Brasília, não do UTC.** A perda diária máxima
  é uma regra de quem opera, e quem opera está no Brasil: uma perda às 22h e
  outra à 1h da manhã são do mesmo dia. Só a definição de "dia" usa o fuso;
  o resto dos cálculos segue em UTC.
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
