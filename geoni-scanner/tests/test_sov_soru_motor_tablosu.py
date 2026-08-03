"""SOV soru-basina motor tablosu (2026-08-03 kurucu karari).

ONCE her motor her soruya giderdi: 5 sorgu x 4 motor = 20 canli arama. Ucret
ARAMA CAGRISI basinadir, dolayisiyla tek gercek tasarruf kaldiraci cagri
sayisidir. Olculdu (13 tarama, birincil buluslar): ChatGPT 6 · Perplexity 1 ·
Claude 1 · Gemini 1 — ChatGPT buluslarin ~%70'ini yapiyor ama arama basi
$0.0298 (Perplexity'nin 5,5 katı, Gemini'nin 21 katı).

KURGU: Perplexity+Gemini her soruda; ChatGPT yalniz ilk IKI birincil soruda;
komsu sorgularda pahali motor yok (komsu zaten SOV paydasina girmiyor — Y6).

Bu testler kurguyu kilitler. Biri kirilirsa maliyet sessizce geri firlar
(ChatGPT'nin her soruya donmesi tarama basina +$0.089 demek) ya da olcum
sessizce zayiflar (ChatGPT'nin tumden dusmesi buluslarin %70'ini goturur).
"""
import asyncio

import sov


def _kosum(pinned):
    """Sahte motorlarla SOV kosar; her motorun HANGI sorguya gittigini kaydeder."""
    cagrilar: dict[str, list[str]] = {}

    def _motor(ad):
        async def _f(query, max_tokens=400):
            cagrilar.setdefault(ad, []).append(query)
            return {"text": "cevap", "citations": []}
        return _f

    async def fake_llm(prompt):
        return None  # rakip cikarimi offline

    sonuc = asyncio.run(sov.check_share_of_voice(
        "Acme", "test alani", _motor("perplexity"), fake_llm,
        ask_google=_motor("google"),
        ask_openai_web=_motor("chatgpt"),
        ask_claude_web=_motor("claude"),
        pinned_queries=pinned,
    ))
    return cagrilar, sonuc


# Kurucunun kurgusu: 3 birincil + 2 komsu
BES_SORU = [
    {"query": "b1", "adjacent": False},
    {"query": "b2", "adjacent": False},
    {"query": "b3", "adjacent": False},
    {"query": "k1", "adjacent": True},
    {"query": "k2", "adjacent": True},
]


def test_ucuz_motorlar_her_soruda():
    """Perplexity+Gemini tabani: $0.0068/soru, olcumun surekliligini saglar."""
    cagrilar, _ = _kosum(BES_SORU)
    assert cagrilar["perplexity"] == ["b1", "b2", "b3", "k1", "k2"]
    assert cagrilar["google"] == ["b1", "b2", "b3", "k1", "k2"]


def test_chatgpt_yalniz_ilk_iki_birincil_soruda():
    """3. birincil ve komsu sorularda ChatGPT YOK — tarama basina $0.089 fark."""
    cagrilar, _ = _kosum(BES_SORU)
    assert cagrilar["chatgpt"] == ["b1", "b2"]


def test_claude_acilirsa_yalniz_bir_soruya_girer():
    """CLAUDE_SOV=1 kacis kapisi eski 'her soru' davranisina DONMEZ.

    Donerse tek env degiskeni tarama maliyetini $0.10 sicratir.
    """
    cagrilar, _ = _kosum(BES_SORU)
    assert cagrilar["claude"] == ["b1"]


def test_engines_used_fiilen_cagrilani_soyler():
    """Rapor 'kayitli motor' degil GERCEKTEN sorulan motorlari listelemeli."""
    _, sonuc = _kosum(BES_SORU)
    assert sonuc["engines_used"] == ["chatgpt", "claude", "google", "perplexity"]


def test_komsu_sorguda_pahali_motor_yok():
    """Komsu sorgu SOV paydasina girmez (Y6); orada pahali motor para yakar."""
    cagrilar, _ = _kosum(BES_SORU)
    for pahali in ("chatgpt", "claude"):
        assert not (set(cagrilar.get(pahali, [])) & {"k1", "k2"})


def test_sorgu_sayisi_degisince_kurgu_korunur():
    """Secim INDEKSE degil `adjacent` bayragina bagli.

    custom_queries/pinned_queries yollarinda sorgu sayisi 5 olmayabiliyor
    (olculdu: ort. 4,56-5,00). Dort birincil sorulu bir sette de ChatGPT yine
    ilk ikisinde kalmali — yoksa sorgu sayisi arttikca maliyet sessizce buyur.
    """
    dort_birincil = [{"query": f"b{i}", "adjacent": False} for i in range(1, 5)]
    cagrilar, _ = _kosum(dort_birincil + [{"query": "k1", "adjacent": True}])
    assert cagrilar["chatgpt"] == ["b1", "b2"]
    assert cagrilar["perplexity"] == ["b1", "b2", "b3", "b4", "k1"]


def test_cagri_sayisi_dustu():
    """Toplam canli arama cagrisi: eski kartezyen 5x4=20.

    Yeni kurgu Claude ACIKKEN 13 (5 perplexity + 5 gemini + 2 chatgpt + 1
    claude). URETIMDE Claude kapali oldugu icin fiili sayi 12 ve SOV ucreti
    5x$0.0054 + 5x$0.0014 + 2x$0.0298 = $0.0936 (+ AI Ozeti $0.0120).
    """
    cagrilar, _ = _kosum(BES_SORU)
    assert sum(len(v) for v in cagrilar.values()) == 13
    claudesuz = sum(len(v) for k, v in cagrilar.items() if k != "claude")
    assert claudesuz == 12
