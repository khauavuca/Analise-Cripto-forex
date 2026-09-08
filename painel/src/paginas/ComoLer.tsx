import type { ReactNode } from "react";
import type { Estado } from "../api";
import { Aviso, Painel } from "../componentes";

function Termo({ nome, children }: { nome: string; children: ReactNode }) {
  return (
    <div className="border-t border-borda py-2.5 first:border-t-0 first:pt-0 last:pb-0">
      <dt className="mono text-[12px] font-medium text-texto">{nome}</dt>
      <dd className="mt-0.5 max-w-prose text-[12.5px] leading-relaxed text-texto-2">{children}</dd>
    </div>
  );
}

export function ComoLer({ estado }: { estado: Estado | null }) {
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <Painel titulo="os três números que só fazem sentido juntos">
        <dl>
          <Termo nome="acerto">
            A fatia das operações fechadas que deram lucro depois das taxas. Sozinho, engana: 90% de acerto com perdas gigantes nos outros 10% perde dinheiro.
          </Termo>
          <Termo nome="faixa do acerto (entre X e Y)">
            Onde o acerto verdadeiro provavelmente está, com 95% de confiança. Com poucas operações a faixa é larga, e é isso que diz o quanto o número ainda é
            incerto. Com 12 operações e 50% de acerto, a faixa vai de 25% a 75%: não dá para afirmar quase nada.
          </Termo>
          <Termo nome="payoff">
            O tamanho médio do ganho dividido pelo tamanho médio da perda. 2,00 quer dizer que cada ganho paga duas perdas. 40% de acerto com payoff 2,00 ganha
            dinheiro; 60% com payoff 0,50 perde.
          </Termo>
          <Termo nome="empata em">
            Junta os dois: é o acerto mínimo para não perder dinheiro com aquele payoff, calculado como 1 ÷ (1 + payoff). Trader bom é o que acerta acima disso
            com folga. A mesa pinta de verde o acerto que está acima e de vermelho o que está abaixo.
          </Termo>
        </dl>
      </Painel>

      <Painel titulo="o resto do vocabulário">
        <dl>
          <Termo nome="média/op">Quanto cada operação rendeu em média, em dinheiro, já com as taxas. É a expectativa por trade em linguagem de banca.</Termo>
          <Termo nome="média em R">
            O mesmo, medido em risco: 1 R é o que se arriscou até o stop. +0,30 R quer dizer que, em média, cada operação devolve 30% do que arriscou. Positivo é
            bom; perto de zero é ruído.
          </Termo>
          <Termo nome="g/p (ganha/perde)">Quanto ganha se bater o alvo para cada 1 que perde se bater o stop. 2,00:1 é o mínimo que a maioria dos setups aceita.</Termo>
          <Termo nome="em aberto">Operação que ainda não bateu nem o alvo nem o stop. Não entra em nenhuma conta até fechar.</Termo>
          <Termo nome="dd (pior queda)">Quanto a banca chegou a cair do seu ponto mais alto. É o que dói de verdade; é o número que tira gente do jogo.</Termo>
          <Termo nome="recusada">Sinal que as regras da banca barraram: teto de posições, já posicionado no par, exposição, perda diária ou pausa depois de uma sequência de perdas.</Termo>
          <Termo nome="tempo">Toda operação tem prazo. Sem isso, as perdedoras ficariam abertas para sempre e o acerto inflaria.</Termo>
        </dl>
      </Painel>

      <Painel titulo="por que 30 operações">
        <p className="max-w-prose text-[12.5px] leading-relaxed text-texto-2">
          Abaixo de 30 operações fechadas, a mesa avisa que a amostra é curta. Não é capricho: com 10 operações, um acerto de 60% é indistinguível de 30% ou de
          85%. A ordem dos traders muda de lugar por sorte. A campanha existe para acumular operações; a ordem só vale quando a amostra crescer, e mesmo aí uma
          semana é um regime de mercado só.
        </p>
      </Painel>

      <Painel titulo="de onde vêm os números">
        <ul className="max-w-prose list-disc space-y-1.5 pl-5 text-[12.5px] leading-relaxed text-texto-2">
          <li>
            <span className="mono text-texto">campanha</span> · refeita do zero a cada leitura, a partir das velas reais entre o início e agora. Cada setup é um trader
            com a própria banca e as mesmas regras. Só contam operações que nasceram dentro da campanha.
          </li>
          <li>
            <span className="mono text-texto">agora</span> · a última vela fechada de cada par passa pelos setups; o que sai é o que o sistema faria, já filtrado pelas
            regras da banca.
          </li>
          <li>
            <span className="mono text-texto">setups</span> · os sinais que a nuvem coletou ao vivo são seguidos vela a vela até o stop ou o alvo, pelo mesmo motor do
            backtest. Não é simulação sobre o passado: é o que aconteceu depois de cada sinal.
          </li>
          <li>
            <span className="mono text-texto">horários</span> · tudo no {estado?.rotulo_fuso ?? "horário de Brasília"}. A corretora trabalha em UTC, 3 horas à frente;
            os dois relógios ficam no topo.
          </li>
        </ul>
      </Painel>

      <div className="xl:col-span-2">
        <Aviso tom="alta" rotulo="regra da casa" titulo="Nenhuma ordem é enviada. Nunca.">
          O sistema lê o mercado, decide o que faria e mede se teria acertado. Executar é decisão de quem opera. Não há chave de API em lugar nenhum: só
          endpoints públicos de preço.
        </Aviso>
      </div>
    </div>
  );
}
