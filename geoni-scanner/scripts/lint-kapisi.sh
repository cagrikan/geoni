#!/bin/bash
# DAĞITIM KAPISI — "tanımsız isim" sınıfını canlıya bırakma.
#
# NEDEN VAR (2026-08-08'de pahalıya patladı): `start_brand_check` ve
# `start_social_check` içinde `_bekleyen_hak[job_id]` satırı, `job_id`
# üretilmeden çalışıyordu → UnboundLocalError → 500 → kullanıcıda "Load failed".
# **4 gün canlıda kaldı** ve ücretsiz hakla yapılan HER marka/sosyal taramayı
# kırdı. Kurucu kendi kullanırken buldu.
#
# Ölçüldü: pyflakes o sürümde iki satırı da ANINDA yakalıyor —
#     main.py:1274:23: undefined name 'job_id'
#     main.py:1360:23: undefined name 'job_id'
# CI yalnız pytest koşuyordu; testler bu yolu hiç çağırmıyordu (iç tarama
# anahtarı ücretsiz-hak kapısını atlıyor). 2 saniyelik lint 4 günü kurtarırdı.
#
# 🪤 KOZMETİK BULGULAR KAPIYI KAPATMAZ. Pakette 17 bulgu var (kullanılmayan
# import, placeholder'sız f-string); bunlar kırıcı değil ve hepsini engel yapmak
# kapıyı ilk günde devre dışı bıraktırır. Yalnız ÇALIŞMA ZAMANINDA PATLAYAN
# sınıf engeldir.
set -uo pipefail
cd "$(dirname "$0")/.."

if ! python -m pyflakes --version >/dev/null 2>&1; then
  echo "lint-kapisi: pyflakes yok, kuruluyor…"
  python -m pip install --quiet pyflakes || { echo "pyflakes kurulamadi — kapi ATLANDI"; exit 0; }
fi

TUM=$(python -m pyflakes ./*.py 2>&1 || true)

# Çalışma zamanında NameError/UnboundLocalError üreten sınıf:
KRITIK=$(printf '%s\n' "$TUM" | grep -E "undefined name|may be undefined|referenced before assignment" || true)

if [ -n "$KRITIK" ]; then
  echo "🔴 DAGITIM DURDURULDU — tanimsiz isim (calisma zamaninda 500 verir):"
  printf '%s\n' "$KRITIK" | sed 's/^/   /'
  echo
  echo "   Bu sinif 2026-08-08'de 4 gun canlida kaldi ve marka/sosyal taramayi kirdi."
  exit 1
fi

ADET=$(printf '%s\n' "$TUM" | grep -c . || true)
echo "✅ lint kapisi gecti — kritik bulgu yok (kozmetik: $ADET)"
exit 0
