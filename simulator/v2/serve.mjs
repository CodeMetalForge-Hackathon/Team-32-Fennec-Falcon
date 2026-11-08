#!/usr/bin/env node
// Serves the simulator on your network and relays Remote GPU signalling between two browsers. No dependencies.
//
//   node simulator/v2/serve.mjs            (port 8080; or: node simulator/v2/serve.mjs 9000)
//
// On the computer with the strong GPU open  http://localhost:8080/#host
// On the laptop open                        http://<that computer's address>:8080/#view
// (the addresses are printed below when it starts). The video goes straight between the two browsers;
// this server only passes the connection offer and answer, and serves files from this folder.
import http from 'node:http';
import { readFile } from 'node:fs/promises';
import { networkInterfaces } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const dir = path.dirname(fileURLToPath(import.meta.url));
const port = Number(process.argv[2] || process.env.PORT || 8080);
const TYPES = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.mjs': 'text/javascript; charset=utf-8',
  '.json': 'application/json', '.css': 'text/css', '.png': 'image/png', '.jpg': 'image/jpeg', '.svg': 'image/svg+xml', '.md': 'text/plain; charset=utf-8' };
const clients = new Map();   // peer id -> { res, presence }
const early = new Map();     // presence posted before that page's event stream connected

function sse(res, event, data) { res.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`); }
function broadcastPeers() { const list = [...clients].map(([id, c]) => ({ id, presence: c.presence })); for (const c of clients.values()) sse(c.res, 'peers', list); }
async function readJson(req) {
  let body = '';
  for await (const chunk of req) { body += chunk; if (body.length > 256 * 1024) throw Object.assign(new Error('too large'), { status: 413 }); }
  return JSON.parse(body || '{}');
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://localhost'), p = url.pathname;
  try {
    if (p.endsWith('/rgpu/hello')) { res.writeHead(200, { 'content-type': 'application/json', 'cache-control': 'no-store' }); return res.end('{"rgpu":1}'); }
    if (p.endsWith('/rgpu/events')) {   // one Server-Sent Events stream per open page
      const id = (url.searchParams.get('peer') || '').replace(/[^a-z0-9]/gi, '').slice(0, 24);
      if (!id) { res.writeHead(400); return res.end(); }
      res.writeHead(200, { 'content-type': 'text/event-stream', 'cache-control': 'no-store', connection: 'keep-alive' });
      res.write(': connected\n\n');
      clients.set(id, { res, presence: early.get(id) || {} }); early.delete(id); broadcastPeers();
      const keepAlive = setInterval(() => res.write(': keep-alive\n\n'), 15000);
      req.on('close', () => { clearInterval(keepAlive); clients.delete(id); broadcastPeers(); });
      return;
    }
    if (req.method === 'POST' && p.endsWith('/rgpu/emit')) {
      const m = await readJson(req), msg = { from: String(m.from || '').slice(0, 24), topic: String(m.topic || '').slice(0, 48), data: m.data };
      for (const c of clients.values()) sse(c.res, 'msg', msg);
      res.writeHead(204); return res.end();
    }
    if (req.method === 'POST' && p.endsWith('/rgpu/presence')) {
      const m = await readJson(req), c = clients.get(String(m.id || ''));
      const pres = m.presence && typeof m.presence === 'object' ? m.presence : {};
      if (c) { c.presence = pres; broadcastPeers(); } else if (m.id && early.size < 64) early.set(String(m.id).slice(0, 24), pres);
      res.writeHead(204); return res.end();
    }
    if (req.method !== 'GET' && req.method !== 'HEAD') { res.writeHead(405); return res.end(); }
    // static files from this folder only
    let rel = decodeURIComponent(p); if (rel.endsWith('/')) rel += 'index.html';
    const file = path.join(dir, path.normalize(rel));
    if (file !== dir && !file.startsWith(dir + path.sep)) { res.writeHead(403); return res.end(); }
    const data = await readFile(file);
    res.writeHead(200, { 'content-type': TYPES[path.extname(file).toLowerCase()] || 'application/octet-stream', 'cache-control': 'no-store' });
    res.end(req.method === 'HEAD' ? undefined : data);
  } catch (err) {
    res.writeHead(err.status || (err.code === 'ENOENT' || err.code === 'EISDIR' ? 404 : 400)); res.end();
  }
});

server.listen(port, () => {
  const addrs = Object.values(networkInterfaces()).flat().filter(a => a && a.family === 'IPv4' && !a.internal).map(a => a.address);
  console.log(`Eagle simulator on port ${port}`);
  console.log(`  GPU computer (this one):  http://localhost:${port}/#host`);
  for (const a of addrs) console.log(`  laptop on the same network: http://${a}:${port}/#view`);
  console.log('  Plain simulator, no streaming: open the same address without #host or #view. Ctrl+C stops.');
});
