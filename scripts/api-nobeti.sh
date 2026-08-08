#!/usr/bin/env bash
# api/ fonksiyonlarinin nobetini kosar.
#
# Neden gecici dizin: bu depoda kok `package.json` YOK. api/*.js dosyalari ESM
# yazimi kullaniyor (Vercel bunu kendisi cozuyor), ama duz `node` icin
# `{"type":"module"}` gerekiyor. Depoya package.json eklemek Vercel'in derleme
# davranisini degistirebilirdi; onun yerine kopyada kosuyoruz.
set -euo pipefail

KOK="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GECICI="$(mktemp -d)"
trap 'rm -rf "$GECICI"' EXIT

cp -R "$KOK/api" "$GECICI/api"
cp "$KOK/scripts/api-nobeti.mjs" "$GECICI/nobet.mjs"
printf '{"type":"module"}' > "$GECICI/package.json"

cd "$GECICI"
node nobet.mjs
