// Instagram webhook alicisi + AI DM asistani: geoni.ai/api/ig-webhook
// - GET: Meta abonelik dogrulamasi
// - POST: imza dogrulanir, olay ig_events'e yazilir; ig_autoreply_enabled='true' ise:
//   * DM'ler: ig_autoreply_mode='ai' -> Claude ile GERCEK SOHBET (gecmis turlar
//     ig_dm_log'dan; AI gorunurlugu/GEONI konusunda bilgili, kisa IG uslubu).
//     mode='template' -> tek satir sablon (yedek).
//   * Yorumlar: tek-seferlik kisa sablon (sohbet DM'de yasar).
// Ayarlar app_config'te (deploy'suz degisir). Idempotency: mesaj mid'i ve
// yorum id'si ig_replies ile tekillestirilir (Meta yeniden gonderimlerine dayanikli).
import crypto from 'crypto';

const GRAPH = 'https://graph.instagram.com/v23.0';
const DAILY_DM_CAP = 15; // gonderen basina gunluk AI cevap tavani (dongu/istismar freni)

function sb(path, init = {}) {
  const key = process.env.SUPABASE_SERVICE_ROLE_KEY;
  return fetch(`${process.env.SUPABASE_URL}/rest/v1/${path}`, {
    ...init,
    headers: {
      apikey: key,
      Authorization: `Bearer ${key}`,
      'Content-Type': 'application/json',
      ...(init.headers || {}),
    },
  });
}

async function getConfig() {
  const keys = 'ig_verify_token,ig_app_secret,ig_access_token,ig_self_id,ig_autoreply_enabled,ig_autoreply_mode,ig_autoreply_comment_text,ig_autoreply_dm_text,ig_autoreply_close_text';
  const r = await sb(`app_config?key=in.(${keys})&select=key,value`);
  const rows = r.ok ? await r.json() : [];
  return Object.fromEntries(rows.map((x) => [x.key, x.value]));
}

/** Idempotency: ayni hedefe ikinci islem yok. Ilk kez ise true. */
async function claim(targetId, kind) {
  const r = await sb('ig_replies?on_conflict=target_id', {
    method: 'POST',
    headers: { Prefer: 'resolution=ignore-duplicates,return=representation' },
    body: JSON.stringify({ target_id: targetId, kind }),
  });
  if (!r.ok) return false;
  const rows = await r.json().catch(() => []);
  return Array.isArray(rows) && rows.length > 0;
}

async function replyToComment(commentId, text, token) {
  await fetch(`${GRAPH}/${commentId}/replies`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ message: text }),
  }).catch(() => {});
}

async function sendDm(recipientId, text, token) {
  await fetch(`${GRAPH}/me/messages`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ recipient: { id: recipientId }, message: { text } }),
  }).catch(() => {});
}

// ── AI DM asistani ─────────────────────────────────────────────────────────

const DM_SYSTEM = `Sen GEONI'nin Instagram DM asistanisin. GEONI (geoni.ai), markalarin,
kisilerin ve web sitelerinin AI cevap motorlarindaki (ChatGPT, Gemini, Perplexity, Claude)
gorunurlugunu olcen ve iyilestiren bir platformdur.

BILGILERIN:
- Nasil olculur: Gercek kullanicilarin soracagi sorular 4 AI motoruna ayni anda sorulur;
  marka/kisi cevaplarda geciyor mu, nasil tanimlaniyor, hangi rakipler oneriliyor bakilir.
- Skorun boyutlari: taninirlik, kategori gorunurlugu (SoV), AI botlarina teknik erisim
  (robots.txt/llms.txt), yapisal veri (schema), dizin kapsami, kaynak guveni. 0-100 skor.
- Ucretsiz tarama: 60 saniyede — site, kisi, marka veya sosyal hesap. Uyelik
  gerekmeden baslar.
- Iyilestirme: uzman hizmetleri var (AI botlarina erisim dosyalari, schema kurulumu,
  AI'larin alintilayacagi icerik, guvenilir kaynaklarda gorunurluk, bilgi tabani kaydi).
  Odeme token'la; detaylar uygulamanin Hizmetler bolumunde.
- YONLENDIRME KURALI: Insanlari GEONI iPhone uygulamasina yonlendir —
  "App Store'dan GEONI'yi indir" de. Net ve kendinden emin; "yakinda" gibi
  ceviriler kullanma.

USLUP: Instagram DM'i gibi yaz — COK KISA: 1-3 kisa cumle, asla 4'u gecme. Tek
konuya odaklan; detay istenirse ac. Samimi ve net. Ekip agziyla konus ("olcuyoruz",
"bakiyoruz"). Kullanici hangi dilde yazdiysa SAF o dilde yaz — Turkce cevaba Ingilizce
kelime serpistirme ("basically" vb. yasak). Turkce dilbilgisi KUSURSUZ olsun —
ozellikle soru eki: "geciyor musunuz?" DOGRU, "geciyorsunuz mu?" YANLIS.
"ya da" AYRI yazilir ("yada" degil).
Emoji olculu (en fazla 1).
Markdown/baslik kullanma. ASLA URL veya alan adi YAZMA — "geoni.ai",
"app.geoni.ai" dahil hicbir adres gecmesin (Instagram her adres icin sohbeti
kirleten bir onizleme balonu acar). Yonlendirme her zaman uygulamaya:
"App Store'da GEONI" — URL'siz, temiz metin.

SINIRLAR: Sadece GEONI ve AI gorunurlugu konusunda konus. Alakasiz konulari kibarca
GEONI'ye baglayarak geri getir. Fiyat rakami verme; uygulamanin Hizmetler
bolumune yonlendir. Bilmedigin
seyde durust ol ve ekibin donecegini soyle. Asla baska bir arac/rakip onerme.
Sohbetin dogal yerinde ucretsiz taramayi hatirlat ama her mesajda tekrarlamaktan kacin.`;

async function loadHistory(senderId, limit = 10) {
  const r = await sb(`ig_dm_log?sender_id=eq.${encodeURIComponent(senderId)}&select=role,text&order=id.desc&limit=${limit}`);
  const rows = r.ok ? await r.json() : [];
  return rows.reverse().map((m) => ({ role: m.role, content: m.text }));
}

async function logDm(senderId, role, text) {
  await sb('ig_dm_log', {
    method: 'POST',
    headers: { Prefer: 'return=minimal' },
    body: JSON.stringify({ sender_id: senderId, role, text: String(text).slice(0, 4000) }),
  }).catch(() => {});
}

async function todaysCount(senderId) {
  const since = new Date();
  since.setUTCHours(0, 0, 0, 0);
  const r = await sb(`ig_dm_log?sender_id=eq.${encodeURIComponent(senderId)}&role=eq.assistant&created_at=gte.${since.toISOString()}&select=id`, {
    headers: { Prefer: 'count=exact', Range: '0-0' },
  });
  const cr = r.headers.get('content-range') || '';
  const total = parseInt(cr.split('/')[1] || '0', 10);
  return Number.isFinite(total) ? total : 0;
}

async function aiReply(senderId, userText, cfg, extraSystem = '') {
  const history = await loadHistory(senderId);
  const messages = [...history, { role: 'user', content: userText }];
  const system = extraSystem ? `${DM_SYSTEM}\n\n${extraSystem}` : DM_SYSTEM;
  const r = await fetch('https://api.anthropic.com/v1/messages', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'x-api-key': process.env.ANTHROPIC_API_KEY,
      'anthropic-version': '2023-06-01',
    },
    body: JSON.stringify({
      model: 'claude-sonnet-5',
      max_tokens: 200,
      system,
      messages,
    }),
  });
  if (!r.ok) return null;
  const data = await r.json();
  const text = (data.content || []).filter((b) => b.type === 'text').map((b) => b.text).join(' ').trim();
  return text || null;
}

/** Bu gonderenle bugunden ONCE konusmusluk var mi? (donus karsilamasi icin) */
async function talkedBeforeToday(senderId) {
  const t = new Date(); t.setUTCHours(0, 0, 0, 0);
  const r = await sb(`ig_dm_log?sender_id=eq.${encodeURIComponent(senderId)}&role=eq.assistant&created_at=lt.${t.toISOString()}&select=id&limit=1`);
  const rows = r.ok ? await r.json() : [];
  return rows.length > 0;
}

async function handleDm(senderId, msg, cfg) {
  const mid = msg.mid ? String(msg.mid) : null;
  const userText = (msg.text || '').trim();
  // Ayni mesaji (Meta tekrar gonderimi) iki kez isleme
  if (mid && !(await claim(`mid:${mid}`, 'dm'))) return;

  if (cfg.ig_autoreply_mode === 'ai' && process.env.ANTHROPIC_API_KEY && userText) {
    await logDm(senderId, 'user', userText);

    const count = await todaysCount(senderId);

    // Gunluk tavana gelindiyse: sessiz kesme yerine BIR KEZ kibar veda +
    // uygulamaya davet; sonraki mesajlarda o gun sessiz kal.
    if (count >= DAILY_DM_CAP) {
      const day = new Date().toISOString().slice(0, 10).replace(/-/g, '');
      if (await claim(`cap:${senderId}:${day}`, 'dm')) {
        const closing = cfg.ig_autoreply_close_text || '';
        if (closing) {
          await sendDm(senderId, closing, cfg.ig_access_token);
          await logDm(senderId, 'assistant', closing);
        }
      }
      return;
    }

    // Daha once konustugumuz biri yeni gun ilk mesajini attiysa: sicak karsilama
    let extra = '';
    if (count === 0 && (await talkedBeforeToday(senderId))) {
      extra = 'NOT: Bu kullaniciyla onceki gun(ler)de konusmustuk, simdi geri geldi. '
        + 'Cevabinin BASINDA samimi TEK cumleyle karsila ve gecmise uygun takip sorusu sor '
        + '(or. taramayi yaptin mi / skoruna bakabildin mi / uygulamayi indirdin mi — '
        + 'gecmis konusmaya hangisi uyuyorsa). Sonra mesajina normal cevap ver.';
    }
    const reply = await aiReply(senderId, userText, cfg, extra);
    if (reply) {
      await sendDm(senderId, reply, cfg.ig_access_token);
      await logDm(senderId, 'assistant', reply);
      return;
    }
  }
  // Yedek: sablon (AI kapali/hata/bos mesaj) — gonderene gunde bir kez
  const day = new Date().toISOString().slice(0, 10).replace(/-/g, '');
  if (await claim(`dm:${senderId}:${day}`, 'dm')) {
    await sendDm(senderId, cfg.ig_autoreply_dm_text || '', cfg.ig_access_token);
  }
}

async function autoReply(body, cfg) {
  if (cfg.ig_autoreply_enabled !== 'true') return;
  const token = cfg.ig_access_token;
  const self = cfg.ig_self_id;
  if (!token || !self) return;

  for (const entry of body.entry || []) {
    if (String(entry.id) === '0') continue; // Meta panel test olaylari

    // DM'ler — gercek akista entry.messaging[], bazi bicimlerde changes[].value
    const dmEvents = [...(entry.messaging || [])];
    for (const c of entry.changes || []) {
      if (c.field === 'messages' && c.value) dmEvents.push(c.value);
    }
    for (const m of dmEvents) {
      const sender = m.sender?.id && String(m.sender.id);
      const isEcho = !!m.message?.is_echo;
      if (!sender || sender === self || isEcho || !m.message) continue;
      await handleDm(sender, m.message, cfg);
    }

    // Yorumlar: kisa tek-seferlik sablon (sohbet DM'de)
    for (const c of entry.changes || []) {
      if (c.field !== 'comments' || !c.value) continue;
      const v = c.value;
      const commentId = v.id && String(v.id);
      const from = v.from?.id && String(v.from.id);
      if (!commentId || !from || from === self) continue;
      if (await claim(`cm:${commentId}`, 'comment')) {
        await replyToComment(commentId, cfg.ig_autoreply_comment_text || '', token);
      }
    }
  }
}

export const config = { api: { bodyParser: false } };

function readRawBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on('data', (c) => chunks.push(c));
    req.on('end', () => resolve(Buffer.concat(chunks)));
    req.on('error', reject);
  });
}

export default async function handler(req, res) {
  const cfg = await getConfig();

  if (req.method === 'GET') {
    const { 'hub.mode': mode, 'hub.verify_token': token, 'hub.challenge': challenge } = req.query;
    if (mode === 'subscribe' && token && token === cfg.ig_verify_token) {
      res.statusCode = 200;
      return res.end(challenge);
    }
    res.statusCode = 403;
    return res.end('verification failed');
  }

  if (req.method !== 'POST') {
    res.statusCode = 405;
    return res.end();
  }

  const raw = await readRawBody(req);

  const sig = req.headers['x-hub-signature-256'] || '';
  const expected = 'sha256=' + crypto.createHmac('sha256', cfg.ig_app_secret || '').update(raw).digest('hex');
  const valid = sig.length === expected.length &&
    crypto.timingSafeEqual(Buffer.from(sig), Buffer.from(expected));
  if (!valid) {
    res.statusCode = 401;
    return res.end('bad signature');
  }

  let body = {};
  try { body = JSON.parse(raw.toString('utf8')); } catch { /* bos birak */ }

  const rows = [];
  for (const entry of body.entry || []) {
    const fields = (entry.changes || []).map((c) => c.field);
    if (entry.messaging) fields.push('messaging');
    rows.push({ object: body.object || null, field: fields.join(',') || null, entry });
  }
  if (rows.length) {
    await sb('ig_events', {
      method: 'POST',
      headers: { Prefer: 'return=minimal' },
      body: JSON.stringify(rows),
    }).catch(() => {});
  }

  try { await autoReply(body, cfg); } catch { /* cevap hatasi 200'u engellemez */ }

  res.statusCode = 200;
  return res.end('ok');
}
