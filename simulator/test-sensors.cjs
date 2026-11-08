const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(__dirname + '/index.html', 'utf8');
const sandbox = {module: {exports: {}}};
vm.runInNewContext(html.match(/<script id="eagle-model">([\s\S]*?)<\/script>/)[1], sandbox);
const M = sandbox.module.exports;
assert.equal(M.defaults().signal, 'none');
for (const preset of Object.values(M.presets)) {
  const r = M.evaluate(preset);
  assert.equal(r.find.identity, 'UNVERIFIED');
  assert(!r.acoustic.detected);
  assert(r.find.by.every(x => ['Acoustic', 'Thermal', 'Radar'].includes(x)));
  assert(r.messages.every(x => ['FIX', 'SBD MO', 'COURIER'].includes(x.name)));
  assert(r.messages.every(x => !x.dir.startsWith('person')));
}
const silentDay = M.evaluate({...M.presets.cicadaDay, opState: 'unconscious', signal: 'shout'});
assert.equal(silentDay.acoustic.signal, 'none');
assert.equal(silentDay.find.status, 'NOT FOUND');
assert.equal(silentDay.timeToMarker_s, null);
assert.equal(silentDay.messages.length, 0);
const voice = M.evaluate({...M.presets.night, signal: 'voice', perch: 'stand', opDist_m: 5});
assert(voice.acoustic.detected);
assert.equal(voice.find.identity, 'UNVERIFIED');
const blockedRelay = M.evaluate({relayDist_m: 1e9});
assert(blockedRelay.thermal.detected);
assert.notEqual(blockedRelay.fix.ce_m, null);
assert(!blockedRelay.rf.linkOK);
assert.equal(blockedRelay.timeToMarker_s, null);
assert.equal(blockedRelay.messages.length, 0);
assert(blockedRelay.timeline.some(x => x.step === 'Store observation'));
assert(!M.evaluate({radarEnabled: true}).radar.detected);
assert(M.evaluate({radarEnabled: true, perch: 'stand', opDist_m: 5}).radar.detected);
assert.equal(M.evaluate({signal: 'invalid'}).acoustic.detected, false);
assert(!Object.hasOwn(M.evaluate({obsoleteSetting: true}).settings, 'obsoleteSetting'));
for (const match of html.matchAll(/<script([^>]*)>([\s\S]*?)<\/script>/g)) {
  if (!/\bsrc=/.test(match[1]) && !/type="module"/.test(match[1])) new vm.Script(match[2]);
}
console.log('PASS: no-equipment sensing, silence/unconsciousness, unverified identity, relay failure, onboard radar limits, configuration and script syntax');
