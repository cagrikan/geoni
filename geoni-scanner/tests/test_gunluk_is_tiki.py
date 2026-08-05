"""Gunluk arka-plan isleri TAKVIME capalanmali, sabit 24 saatlik uykuya degil.

NE OLDU (canli kusur, 2026-08-05'te olculdu):
`improvement_loop` her turun sonunda `asyncio.sleep(24 * 3600)` yapiyordu.
App Runner bos ornegin vCPU'sunu askiya aldigi icin (o gun olculen log
sessizlikleri: 143 / 144 / 143 / 123 dakika) bu uyku takvim gunuyle hizali
kalmiyor, her donuste ileri kayiyordu:

    07-27 12:38 -> 07-29 14:03 -> 08-03 07:40 -> 08-04 09:16 -> 08-05: HIC

Sonuc: `job_last_run:improvement` 2026-08-04'te takildi; ayni surecte yasayan
`retention` ise 2026-08-05 00:09'da kosmustu — cunku o, monitor_loop'un SAATLIK
tikinde `_claim_daily_job` deniyor. Yani kusur platformda degil, desendeydi.

KURAL: "gunde bir kez" kararini SADECE `_claim_daily_job` verir (takvim gunu +
coklu-instance guvenli kilit). Dongunun uykusu yalnizca "ne siklikta bakilir"
sorusunu cevaplar ve bir gunun tamamini orten uzunlukta OLAMAZ — yoksa kacan
gun bir sonraki tike kadar telafi edilemez.
"""
import inspect
import re

import monitor
import self_improve

# Bir tik en fazla bu kadar olabilir: gunun anlamli bir dilimini asarsa
# kacirilan gun telafi edilemez hale gelir.
AZAMI_TIK_SANIYE = 6 * 3600


def test_improvement_tiki_bir_gunden_kisa():
    assert self_improve.IMPROVEMENT_TICK_SECONDS <= AZAMI_TIK_SANIYE, (
        "improvement_loop'un tiki gun-boyu uzunluga cikti; kacan gun telafi "
        "edilemez (bkz. 2026-08-05 canli kusuru)")


def test_monitor_tiki_bir_gunden_kisa():
    """retention ayni kilit desenini kullaniyor; o da capalanmis kalsin."""
    assert monitor.MONITOR_CYCLE_SECONDS <= AZAMI_TIK_SANIYE


def test_improvement_loop_gunluk_kilidi_kullanir():
    kaynak = inspect.getsource(self_improve.improvement_loop)
    assert "_claim_daily_job" in kaynak, (
        "gunde-bir-kez karari kilitle verilmeli, uykuyla degil")


def test_improvement_loop_sabit_24_saat_uyumaz():
    """Regresyon capasi: birisi tiki tekrar 24 saate cekerse burada patlasin."""
    kaynak = inspect.getsource(self_improve.improvement_loop)
    for eslesme in re.finditer(r"asyncio\.sleep\(\s*([^)]+?)\s*\)", kaynak):
        ifade = eslesme.group(1)
        # Sabit sayi degilse (ornegin sabit adi) degerlendirmeye calisma.
        try:
            deger = eval(ifade, {"__builtins__": {}}, {})   # noqa: S307 - sabit ifade
        except Exception:
            continue
        assert deger <= AZAMI_TIK_SANIYE, (
            f"improvement_loop icinde {deger}s'lik sabit uyku var — takvime "
            "capalanmiyor, kacan gunu telafi etmez")
