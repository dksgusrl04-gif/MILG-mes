/* MILG MES 2.0 — 신뢰 계층 (회사 · 서명키 · 해시 · 신뢰 원장)
 * ───────────────────────────────────────────────────────────────
 * · 회사마다 서명키(ECDSA P-256)를 둔다. 데모에서는 브라우저에서 만들고, 실제 운영에서는 각 회사 서버에만 비밀키가 있다.
 * · 모든 기록(계약 · LOT 여권 · 증명서 · 검수 · 에이전트 조치)은 SHA-256 해시로 묶고 회사 서명을 붙인다.
 * · 신뢰 원장(ledger): 기록마다 «앞 기록의 해시» 를 함께 넣어 사슬로 잇는다 → 중간 기록 하나만 고쳐도 그 뒤가 전부 깨져 탐지된다.
 *   블록체인 없이 위변조를 탐지하는 구조. 필요하면 마지막 해시를 외부(공증·타임스탬프)에 고정할 수 있다.
 */
(function () {
  'use strict';
  const enc = new TextEncoder();
  const subtle = (window.crypto && window.crypto.subtle) || null;

  // ── 정규화 JSON (키 정렬) — 같은 내용이면 언제나 같은 해시
  const canon = v => {
    if (v === null || typeof v !== 'object') return JSON.stringify(v === undefined ? null : v);
    if (Array.isArray(v)) return '[' + v.map(canon).join(',') + ']';
    return '{' + Object.keys(v).filter(k => v[k] !== undefined).sort().map(k => JSON.stringify(k) + ':' + canon(v[k])).join(',') + '}';
  };

  // ── SHA-256 (보안 연결이 아니어서 WebCrypto 가 없을 때를 위한 순수 JS 구현 포함)
  function sha256js(str) {
    const K = [0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
      0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da, 0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
      0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
      0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2];
    const b = enc.encode(str), l = b.length, n = ((l + 9 + 63) >> 6) << 6, m = new Uint8Array(n);
    m.set(b); m[l] = 0x80; const dv = new DataView(m.buffer); dv.setUint32(n - 4, l * 8); dv.setUint32(n - 8, Math.floor(l / 0x20000000));
    let h = [0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19];
    const w = new Uint32Array(64), r = (x, c) => (x >>> c) | (x << (32 - c));
    for (let o = 0; o < n; o += 64) {
      for (let i = 0; i < 16; i++) w[i] = dv.getUint32(o + i * 4);
      for (let i = 16; i < 64; i++) { const s0 = r(w[i - 15], 7) ^ r(w[i - 15], 18) ^ (w[i - 15] >>> 3), s1 = r(w[i - 2], 17) ^ r(w[i - 2], 19) ^ (w[i - 2] >>> 10); w[i] = (w[i - 16] + s0 + w[i - 7] + s1) | 0; }
      let [a, bb, c, d, e, f, g, hh] = h;
      for (let i = 0; i < 64; i++) {
        const t1 = (hh + (r(e, 6) ^ r(e, 11) ^ r(e, 25)) + ((e & f) ^ (~e & g)) + K[i] + w[i]) | 0, t2 = ((r(a, 2) ^ r(a, 13) ^ r(a, 22)) + ((a & bb) ^ (a & c) ^ (bb & c))) | 0;
        hh = g; g = f; f = e; e = (d + t1) | 0; d = c; c = bb; bb = a; a = (t1 + t2) | 0;
      }
      h = h.map((x, i) => (x + [a, bb, c, d, e, f, g, hh][i]) | 0);
    }
    return h.map(x => (x >>> 0).toString(16).padStart(8, '0')).join('');
  }
  const hex = buf => [...new Uint8Array(buf)].map(x => x.toString(16).padStart(2, '0')).join('');
  async function sha256(str) { if (subtle) { try { return hex(await subtle.digest('SHA-256', enc.encode(str))); } catch (e) { /* 아래로 */ } } return sha256js(str); }
  const b64 = buf => btoa(String.fromCharCode(...new Uint8Array(buf)));
  const unb64 = s => Uint8Array.from(atob(s), c => c.charCodeAt(0));

  // ── 회사 (데모 공급망: 완성차 → 1차 → 우리 → 소재)
  const COMPANIES = [
    { id: 'OEM-A', name: '완성차 A', short: 'A', tier: 0, role: '완성차 조립', color: '#0E7490' },
    { id: 'T1-B', name: '1차 협력사 B', short: 'B', tier: 1, role: '시트 어셈블리', color: '#9333EA' },
    { id: 'MLNG', name: 'MILG 데모 공장', short: 'C', tier: 2, role: '시트 트랙 스토퍼 브래킷', color: '#4F46E5', self: true },
    { id: 'MAT-D', name: '강판 공급사 D', short: 'D', tier: 3, role: '냉연 강판 코일', color: '#B54708' },
  ];
  const co = id => COMPANIES.find(c => c.id === id) || { id, name: id, short: '?', color: '#64748B' };

  const T = window.MLNGTrust = {
    COMPANIES, co, canon, sha256, sha256js,
    me: 'MLNG', pubkeys: {}, persist: false,              // 지금 화면을 보는 회사 (데모: 회사를 바꿔 가며 상대 시점을 볼 수 있다)
    ready: false, keys: {}, ledger: [], tampered: null, cryptoOk: !!subtle,
  };

  // ── 서명키
  async function makeKeys() {
    for (const c of COMPANIES) {
      if (!subtle) { T.keys[c.id] = { fp: (await sha256('demo-key-' + c.id)).slice(0, 16), demo: true }; continue; }
      const kp = await subtle.generateKey({ name: 'ECDSA', namedCurve: 'P-256' }, true, ['sign', 'verify']);
      const jwk = await subtle.exportKey('jwk', kp.publicKey);
      T.keys[c.id] = { priv: kp.privateKey, pub: kp.publicKey, jwk, fp: (await sha256(canon({ crv: jwk.crv, x: jwk.x, y: jwk.y }))).slice(0, 16) };
    }
  }
  // 서명: 회사 비밀키로 (내용의 해시) 에 서명
  T.sign = async (cid, obj) => {
    const h = await sha256(canon(obj)), k = T.keys[cid];
    if (!k) throw new Error('서명키 없음: ' + cid);
    if (!k.priv) return { alg: 'DEMO-HMAC', kid: k.fp, hash: h, sig: (await sha256(k.fp + h)).slice(0, 64) };
    const sig = await subtle.sign({ name: 'ECDSA', hash: 'SHA-256' }, k.priv, enc.encode(h));
    return { alg: 'ES256', kid: k.fp, hash: h, sig: b64(sig) };
  };
  // 검증: 내용 해시가 같은지 + 서명이 그 회사 공개키로 맞는지
  T.verify = async (cid, obj, s) => {
    if (!s) return { ok: false, why: '서명 없음' };
    const h = await sha256(canon(obj)), k = (T.keys[cid] && T.keys[cid].fp === s.kid) ? T.keys[cid] : T.pubkeys[s.kid];
    if (h !== s.hash) return { ok: false, why: '내용이 서명 이후 바뀌었습니다 (해시 불일치)' };
    if (!k) return { ok: false, why: '서명한 회사의 공개키를 모릅니다' };
    if (k.company && k.company !== cid) return { ok: false, why: '다른 회사의 키로 서명되었습니다' };
    if (!k.pub) return { ok: (await sha256(k.fp + h)).slice(0, 64) === s.sig, why: '데모 서명' };
    const ok = await subtle.verify({ name: 'ECDSA', hash: 'SHA-256' }, k.pub, unb64(s.sig), enc.encode(h));
    return { ok, why: ok ? '' : '서명이 맞지 않습니다' };
  };

  // ── 신뢰 원장 (해시 사슬)
  const head = e => ({ seq: e.seq, at: e.at, company: e.company, kind: e.kind, ref: e.ref, title: e.title, payload: e.payload, prev: e.prev });
  T.append = async (company, kind, ref, title, payload) => {
    const prev = T.ledger.length ? T.ledger[T.ledger.length - 1].hash : '0'.repeat(64);
    const e = { seq: T.ledger.length + 1, at: T.stamp(), company, kind, ref, title, payload: await sha256(canon(payload || {})), prev };
    e.hash = await sha256(canon(head(e)));
    e.sig = await T.sign(company, { hash: e.hash });
    T.ledger.push(e);
    if (T.persist && window.MLNGData && window.MLNGData.v2Append) window.MLNGData.v2Append(e).catch(err => console.warn('[trust] 원장 저장 실패', err.message));
    return e;
  };
  // 사슬 전체 검증 → { ok, n, broken:[{seq, why}] }
  T.verifyLedger = async () => {
    const broken = []; let prev = '0'.repeat(64);
    for (const e of T.ledger) {
      if (e.prev !== prev) broken.push({ seq: e.seq, why: '앞 기록과 사슬이 끊겼습니다' });
      const h = await sha256(canon(head(e)));
      if (h !== e.hash) broken.push({ seq: e.seq, why: '기록 내용이 바뀌었습니다 (해시 불일치)' });
      else { const v = await T.verify(e.company, { hash: e.hash }, e.sig); if (!v.ok) broken.push({ seq: e.seq, why: v.why }); }
      prev = e.hash;
    }
    return { ok: !broken.length, n: T.ledger.length, broken };
  };
  // 위변조 시험: 기록 하나의 내용을 몰래 바꿔 본다 / 되돌린다
  T.tamper = seq => { const e = T.ledger.find(x => x.seq === seq); if (!e || T.tampered) return; T.tampered = { seq, title: e.title }; e.title = e.title + ' (몰래 수정됨)'; };
  T.restore = () => { if (!T.tampered) return; const e = T.ledger.find(x => x.seq === T.tampered.seq); if (e) e.title = T.tampered.title; T.tampered = null; };

  // 시각: 데모는 데이터 기준 시각의 날짜 + 지금 시:분
  T.stamp = () => { const M = window.MLNG; const d = M && M.now ? M.now() : new Date(), t = new Date(); return `${d.toLocaleString('sv-SE').slice(0, 10)} ${t.toTimeString().slice(0, 8)}`; };
  T.short = h => h ? `${h.slice(0, 8)}…${h.slice(-4)}` : '—';

  // ── 시작: 키 만들고, 각 모듈이 등록한 초기화(seed)를 차례로
  const inits = [];
  T.onInit = fn => inits.push(fn);
  T.init = async () => {
    if (T.ready) return;
    await makeKeys();
    const D = window.MLNGData; let serverLen = 0;
    // 실시간: 서버에 쌓인 원장과 공개키를 먼저 읽는다 (예전 세션 키로 서명한 기록도 검증 가능)
    if (D && D.LIVE && D.v2LoadLedger) { try { const r = await D.v2LoadLedger();
      for (const k of r.keys) if (subtle && k.jwk) T.pubkeys[k.kid] = { company: k.company, fp: k.kid, pub: await subtle.importKey('jwk', k.jwk, { name: 'ECDSA', namedCurve: 'P-256' }, true, ['verify']) };
      T.ledger.push(...r.ledger); serverLen = T.ledger.length;
      for (const c of COMPANIES) { const k = T.keys[c.id]; if (k.jwk) await D.v2Key({ kid: k.fp, company: c.id, jwk: { kty: k.jwk.kty, crv: k.jwk.crv, x: k.jwk.x, y: k.jwk.y }, at: T.stamp() }); }
    } catch (e) { console.warn('[trust] 원장 읽기 실패', e); } }
    // 데모 시나리오(계약·발주 등)는 화면 안에서만 — 서버 원장에는 사람이 실제로 한 동작부터 쌓는다
    for (const fn of inits) { try { await fn(); } catch (e) { console.warn('[trust] 초기화 실패', e); } }
    if (D && D.LIVE) T.ledger.splice(serverLen);   // 데모 시나리오 기록은 서버 사슬에 섞지 않는다
    T.persist = !!(D && D.LIVE); T.ready = true;
  };
})();
