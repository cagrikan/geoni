import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import test from 'node:test';

const read = (path) => readFile(new URL(`../${path}`, import.meta.url), 'utf8');

test('public pages load the consent layer', async () => {
  for (const page of ['index.html', 'en.html', 'privacy.html']) {
    const html = await read(page);
    assert.match(html, /href="\/analytics-consent\.css"/);
    assert.match(html, /src="\/analytics-consent\.js" defer/);
  }
});

test('analytics is restricted to consented public page views', async () => {
  const source = await read('analytics-consent.js');

  assert.match(source, /G-2MXW6CXM1N/);
  assert.match(source, /new Set\(\['\/', '\/en', '\/privacy'\]\)/);
  assert.match(source, /window\.location\.search !== ''/);
  assert.match(source, /window\.location\.hash !== ''/);
  assert.match(source, /analytics_storage: 'denied'/);
  assert.match(source, /ad_storage: 'denied'/);
  assert.match(source, /allow_google_signals', false/);
  assert.match(source, /allow_ad_personalization_signals: false/);
  assert.match(source, /send_page_view: false/);
  assert.match(source, /page_referrer: ''/);
  assert.match(source, /gtag\/js\?id=/);
  assert.ok(source.indexOf("value === 'granted'") < source.indexOf('loadAnalytics();'));
});

test('privacy notice explains opt-in, withdrawal and exclusions', async () => {
  const html = await read('privacy.html');

  assert.match(html, /yalnız açıkça kabul ederseniz Google Analytics/);
  assert.match(html, /“Analiz tercihleri”/);
  assert.match(html, /Hesap, tarama, rapor, ödeme veya uygulama içeriği Analytics'e gönderilmez/);
  assert.match(html, /Reklam kişiselleştirme, Google Signals ve çapraz site izleme kapalıdır/);
});
