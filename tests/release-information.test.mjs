import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import handler, { _test } from '../api/surum.js';

function call(method = 'GET') {
  const res = {
    headers: {},
    setHeader(k, v) { this.headers[k] = v; },
    status(code) { this.code = code; return this; },
    json(body) { this.body = body; return this; },
  };
  handler({ method }, res);
  return res;
}

test('only published releases are announced; Android internal release is excluded', () => {
  const res = call();
  assert.equal(res.code, 200);
  assert.deepEqual(res.body, {
    ios: { surum: '1.0.9', enAz: '1.0.0' },
    android: { surum: '1.0.8', enAz: '1.0.0' },
  });
  assert.match(res.headers['Cache-Control'], /s-maxage=600/);
});

test('unsupported methods and impossible minimum versions are rejected', () => {
  assert.equal(call('POST').code, 405);
  assert.equal(call('POST').headers.Allow, 'GET');
  const previous = _test.SURUMLER.ios.enAz;
  try {
    _test.SURUMLER.ios.enAz = '9.0.0';
    assert.equal(call().body.ios, undefined);
    assert.ok(call().body.android);
  } finally {
    _test.SURUMLER.ios.enAz = previous;
  }
});

test('both download pages describe both stores and match published versions', async () => {
  for (const page of ['indir.html', 'download.html']) {
    const html = await readFile(new URL('../' + page, import.meta.url), 'utf8');
    const json = html.match(/<script type="application\/ld\+json">\s*([\s\S]*?)<\/script>/)[1];
    const apps = JSON.parse(json)['@graph'].filter(x => x['@type'] === 'MobileApplication');
    assert.equal(apps.length, 2);
    for (const [platform, os] of [['ios', 'iOS'], ['android', 'Android']]) {
      const app = apps.find(x => x.operatingSystem === os);
      assert.ok(app);
      assert.equal(app.softwareVersion, call().body[platform].surum);
      assert.match(app.downloadUrl, platform === 'ios' ? /apps.apple.com/ : /play.google.com/);
      assert.ok(html.includes('href="' + app.downloadUrl + '"'));
    }
    assert.doesNotMatch(html, /\/\/ iPhone App|\/\/ iPhone Uygulaması/);
  }
});
