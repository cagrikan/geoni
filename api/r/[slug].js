// Kisa link yonlendirici: geoni.ai/r/<slug> -> admin panelde tanimlanan
// hedef URL'e, UTM parametreleri eklenmis sekilde yonlendirir (orn.
// Instagram bio linki icin). Kampanyalar Supabase'deki "campaigns"
// tablosunda tutulur, admin panelden yonetilir.
const FALLBACK_URL = 'https://geoni.ai';

export default async function handler(req, res) {
  const { slug } = req.query;
  const supabaseUrl = process.env.SUPABASE_URL;
  const serviceKey = process.env.SUPABASE_SERVICE_ROLE_KEY;

  if (!slug || !supabaseUrl || !serviceKey) {
    res.writeHead(302, { Location: FALLBACK_URL });
    return res.end();
  }

  try {
    const r = await fetch(
      `${supabaseUrl}/rest/v1/campaigns?slug=eq.${encodeURIComponent(slug)}&select=*&limit=1`,
      { headers: { apikey: serviceKey, Authorization: `Bearer ${serviceKey}` } }
    );
    const rows = r.ok ? await r.json() : [];
    const campaign = rows[0];

    if (!campaign) {
      res.writeHead(302, { Location: FALLBACK_URL });
      return res.end();
    }

    // Tiklamayi say. Yanit gonderilip fonksiyon sonlandiktan sonra
    // bekletilmeyen (awaitsiz) bir istek serverless ortamda tamamlanmadan
    // kesilebilir, bu yuzden yonlendirmeden once bekleniyor - hata olursa
    // da yonlendirmeyi engellemesin diye ayrica yakalaniyor.
    try {
      await fetch(`${supabaseUrl}/rest/v1/rpc/increment_campaign_click`, {
        method: 'POST',
        headers: { apikey: serviceKey, Authorization: `Bearer ${serviceKey}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ p_slug: slug }),
      });
    } catch { /* click sayimi basarisiz olsa da yonlendirme devam etsin */ }

    const target = new URL(campaign.target_url || FALLBACK_URL);
    if (campaign.utm_source) target.searchParams.set('utm_source', campaign.utm_source);
    if (campaign.utm_medium) target.searchParams.set('utm_medium', campaign.utm_medium);
    if (campaign.utm_campaign) target.searchParams.set('utm_campaign', campaign.utm_campaign);

    res.writeHead(302, { Location: target.toString() });
    return res.end();
  } catch {
    res.writeHead(302, { Location: FALLBACK_URL });
    return res.end();
  }
}
