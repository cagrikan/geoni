import test from 'node:test';
import assert from 'node:assert/strict';
import { createHmac } from 'node:crypto';
import { Readable } from 'node:stream';
import handler from '../api/ig-webhook.js';

for (const status of [403, 429, 500, 200, 201, 0]) {
  test(`assistant log requires Meta acceptance: ${status}`, async (t) => {
    const old = process.env.ANTHROPIC_API_KEY;
    process.env.ANTHROPIC_API_KEY = 'test-only';
    t.after(() => { if (old === undefined) delete process.env.ANTHROPIC_API_KEY; else process.env.ANTHROPIC_API_KEY = old; });
    const logs = []; let sent = 0;
    t.mock.method(globalThis, 'fetch', async (input, init = {}) => {
      const url = String(input);
      if (url.includes('graph.instagram.com')) {
        sent++;
        if (!status) throw new Error('secret network failure');
        return Response.json(status === 200 ? { message_id: 'accepted' } : {}, { status });
      }
      if (url.includes('api.anthropic.com')) return Response.json({ content: [{ type: 'text', text: 'Merhaba' }] });
      if (url.includes('app_config')) return Response.json(Object.entries({ ig_app_secret: 'test-secret', ig_access_token: 'fixture', ig_self_id: 'self', ig_autoreply_enabled: 'true', ig_autoreply_mode: 'ai' }).map(([key, value]) => ({ key, value })));
      if (url.includes('ig_dm_log') && init.method === 'POST') logs.push(JSON.parse(init.body));
      if (url.includes('ig_replies')) return Response.json([{ id: 1 }]);
      return Response.json([], { headers: { 'content-range': '0-0/0' } });
    });
    const body = JSON.stringify({ object: 'instagram', entry: [{ id: 'self', messaging: [{ sender: { id: 'owned-fixture' }, message: { mid: 'fixture-mid', text: 'Merhaba' } }] }] });
    const req = Readable.from([Buffer.from(body)]);
    req.method = 'POST'; req.headers = { 'x-hub-signature-256': 'sha256=' + createHmac('sha256', 'test-secret').update(body).digest('hex') };
    const res = { statusCode: 0, end() {} };
    await handler(req, res);
    assert.equal(res.statusCode, 200);
    assert.equal(sent, 1);
    assert.equal(logs.filter(x => x.role === 'assistant').length, status === 200 ? 1 : 0);
  });
}
