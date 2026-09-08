import { useCallback, useEffect, useRef, useState } from "react";

// Carrega dados de uma funcao assincrona e expoe estado + recarga.
// `chave` muda -> recarrega. `recarregar(true)` pede ao servidor dados novos.
export function usarDados<T>(carregar: (atualizar: boolean) => Promise<T>, chave: string) {
  const [dados, setDados] = useState<T | null>(null);
  const [erro, setErro] = useState<Error | null>(null);
  const [carregando, setCarregando] = useState(true);
  const ultima = useRef(0);

  const recarregar = useCallback(
    async (atualizar = false) => {
      const id = ++ultima.current;
      setCarregando(true);
      setErro(null);
      try {
        const resultado = await carregar(atualizar);
        if (id === ultima.current) setDados(resultado);
      } catch (e) {
        if (id === ultima.current) setErro(e instanceof Error ? e : new Error(String(e)));
      } finally {
        if (id === ultima.current) setCarregando(false);
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [chave],
  );

  useEffect(() => {
    void recarregar(false);
  }, [recarregar]);

  return { dados, erro, carregando, recarregar };
}
