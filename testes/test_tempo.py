"""Hora de quem le: UTC por dentro, Brasilia por fora, num lugar so."""
from __future__ import annotations

from datetime import date, datetime, timezone

import pandas as pd

from nucleo import tempo

BRASILIA = tempo.fuso("America/Sao_Paulo")


class TestConversao:
    def test_instante_utc_vira_hora_de_brasilia(self):
        momento = datetime(2026, 9, 5, 1, 37, tzinfo=timezone.utc)
        assert tempo.formatar(momento, "%d/%m %H:%M", BRASILIA) == "04/09 22:37"

    def test_timestamp_do_pandas_tambem(self):
        vela = pd.Timestamp("2026-09-05 00:00", tz="UTC")
        assert tempo.formatar(vela, "%d/%m %H:%M", BRASILIA) == "04/09 21:00"

    def test_valor_sem_fuso_e_tratado_como_utc(self):
        assert tempo.local(pd.Timestamp("2026-09-05 03:00"), BRASILIA).hour == 0
        assert tempo.local(datetime(2026, 9, 5, 3, 0), BRASILIA).hour == 0

    def test_dia_civil_e_o_de_brasilia(self):
        assert tempo.dia(pd.Timestamp("2026-09-05 02:59", tz="UTC"), BRASILIA) == date(2026, 9, 4)
        assert tempo.dia(pd.Timestamp("2026-09-05 03:00", tz="UTC"), BRASILIA) == date(2026, 9, 5)


class TestDatasDigitadas:
    def test_inicio_do_dia_e_meia_noite_de_brasilia_em_utc(self):
        esperado = datetime(2026, 9, 5, 3, 0, tzinfo=timezone.utc)
        assert tempo.inicio_do_dia("2026-09-05", BRASILIA) == esperado

    def test_fim_do_dia_inclui_o_dia_inteiro(self):
        esperado = datetime(2026, 9, 12, 3, 0, tzinfo=timezone.utc)
        assert tempo.fim_do_dia("2026-09-11", BRASILIA) == esperado

    def test_em_utc_nada_muda(self):
        utc = tempo.fuso("UTC")
        assert tempo.inicio_do_dia("2026-09-05", utc) == datetime(2026, 9, 5, tzinfo=timezone.utc)


class TestConfiguracao:
    def test_padrao_e_brasilia(self, monkeypatch):
        monkeypatch.delenv("FUSO_HORARIO", raising=False)
        assert tempo.fuso().key == "America/Sao_Paulo"
        assert tempo.rotulo() == "horario de Brasilia"

    def test_variavel_de_ambiente_troca_o_fuso(self, monkeypatch):
        monkeypatch.setenv("FUSO_HORARIO", "UTC")
        assert tempo.fuso().key == "UTC"
        assert tempo.deslocamento(datetime(2026, 9, 5, tzinfo=timezone.utc)) == "igual ao UTC"

    def test_deslocamento_em_portugues(self):
        frase = tempo.deslocamento(datetime(2026, 9, 5, tzinfo=timezone.utc), BRASILIA)
        assert frase == "o UTC da corretora esta 3 horas a frente"


class TestQuadro:
    def test_converte_toda_coluna_de_data_e_preserva_o_original(self):
        quadro = pd.DataFrame(
            {
                "momento": pd.to_datetime(["2026-09-05 01:00"]).tz_localize("UTC"),
                "entrada": pd.to_datetime(["2026-09-05 02:00"]),  # sem fuso: e UTC
                "valor": [1.0],
            }
        )
        convertido = tempo.quadro_no_fuso(quadro, BRASILIA)
        assert convertido["momento"].iloc[0].hour == 22
        assert convertido["entrada"].iloc[0].hour == 23
        assert convertido["valor"].iloc[0] == 1.0
        assert str(quadro["momento"].dt.tz) == "UTC"
