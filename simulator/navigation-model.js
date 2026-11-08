/* Educational kinematic model. No RF propagation, VIO, EKF or flight controller. */
(function (root) {
  'use strict';
  const route = [[0,0],[0,20],[15,20],[15,0],[30,0],[30,20],[0,0]];
  function create() {
    return {t:0, x:0, y:0, waypoint:1, distance:0, outage:0, uncertainty:1.5,
      mode:'GPS', action:'Follow survey route', stopped:false, complete:false,
      gps:'available', backup:'good', events:[], trail:[[0,0]], estimated:[0,0]};
  }
  function step(old, dt, options = {}) {
    if (!Number.isFinite(dt) || dt < 0 || dt > 0.25) throw new Error('dt must be 0..0.25 seconds');
    const s = {...old, events:old.events.slice(), trail:old.trail.slice()};
    if (s.stopped || s.complete) return s;
    s.t += dt;
    const scenario = options.scenario || 'jamming';
    s.gps = s.t < 10 || scenario === 'clear' ? 'available' : scenario === 'spoofing' ? 'inconsistent' : scenario === 'canopy' ? 'degraded' : 'unavailable';
    s.backup = scenario === 'backup-loss' && s.t >= 25 ? 'lost' : scenario === 'canopy' && s.t >= 20 ? 'degraded' : 'good';
    const gpsOK = s.gps === 'available';
    s.outage = !gpsOK && s.backup === 'lost' ? s.outage + dt : 0;
    s.mode = gpsOK ? 'GPS' : s.backup === 'good' ? 'Visual + inertial' : s.backup === 'degraded' ? 'Visual + inertial · degraded' : 'Inertial only';
    // Illustrative error budget, NOT measured accuracy or a confidence interval.
    s.uncertainty = gpsOK ? 1.5 : s.uncertainty + dt * (s.backup === 'good' ? 0.035 : s.backup === 'degraded' ? 0.22 : 0.65 + s.outage * 0.2);
    if (s.outage >= 5 || s.uncertainty >= 8) {
      s.stopped = true; s.mode = 'Navigation unavailable';
      s.action = 'Abort transit · flight controller must execute a configured failsafe';
    } else s.action = s.backup === 'lost' ? 'Brief dead reckoning · no reliable position hold' : 'Follow survey route';
    if (s.mode !== old.mode) s.events.push({t:s.t, text:s.mode + ' — ' + s.action});
    if (!s.stopped) {
      const target = route[s.waypoint], dx = target[0]-s.x, dy = target[1]-s.y, d = Math.hypot(dx,dy);
      const travel = Math.min(d, dt * (s.backup === 'degraded' ? 0.7 : 1.5));
      if (d > 0) { s.x += dx/d*travel; s.y += dy/d*travel; }
      s.distance += travel;
      if (d <= travel + 1e-8) { s.waypoint++; if (s.waypoint === route.length) { s.complete=true; s.action='Survey completed · returned to local origin'; } }
    }
    const bias = gpsOK ? 0 : (s.uncertainty-1.5)*0.6;
    s.estimated = [s.x+bias, s.y+bias*0.4];
    if (s.trail.length < 3000 && Math.hypot(s.x-s.trail.at(-1)[0],s.y-s.trail.at(-1)[1]) > 0.3) s.trail.push([s.x,s.y]);
    s.georeferenced = options.originKnown !== false;
    return s;
  }
  const api = {route, create, step};
  if (typeof module !== 'undefined') module.exports = api;
  else root.EagleNavigation = api;
})(globalThis);
