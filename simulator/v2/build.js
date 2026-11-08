// Builds index.html: inlines model.js, eagle_drone.js and main's navigation modules (../forest-site.js,
// ../forest-model.js, ../navigation-model.js) into index.src.html and wraps it in a standalone page.
// Also builds eagle_viewer.html from eagle_viewer.src.html. Run after editing any of them: node build.js
const fs = require('fs'), path = require('path');
const dir = __dirname;
const src = fs.readFileSync(path.join(dir, 'index.src.html'), 'utf8');
const model = fs.readFileSync(path.join(dir, 'model.js'), 'utf8');
const eagle = fs.readFileSync(path.join(dir, 'eagle_drone.js'), 'utf8');
if (model.includes('</script')) throw new Error('model.js contains </script');
if (eagle.includes('</script')) throw new Error('eagle_drone.js contains </script');
if (!src.includes('/*@@MODEL@@*/')) throw new Error('index.src.html has no /*@@MODEL@@*/ marker');
if (!src.includes('/*@@EAGLE@@*/')) throw new Error('index.src.html has no /*@@EAGLE@@*/ marker');
// the navigation brain from the unified mission on main, unchanged: forest site, forest planner + state machine,
// position-source model (simulator/*.js, shared with ../index.html and the Node tests there)
const NAV_MODULES = [['/*@@FORESTSITE@@*/', '../forest-site.js'], ['/*@@FOREST@@*/', '../forest-model.js'], ['/*@@NAVMODEL@@*/', '../navigation-model.js']];
let body = src.replace('/*@@MODEL@@*/', () => model).replace('/*@@EAGLE@@*/', () => eagle);
for (const [mark, file] of NAV_MODULES) {
  const js = fs.readFileSync(path.join(dir, file), 'utf8');
  if (js.includes('</script')) throw new Error(file + ' contains </script');
  if (!body.includes(mark)) throw new Error('index.src.html has no ' + mark + ' marker');
  body = body.replace(mark, () => js);
}
const page = '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n' +
  '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n' +
  '<style>body{margin:0}[hidden]{display:none!important}</style>\n</head>\n<body>\n' +
  body + '\n</body>\n</html>\n';
fs.writeFileSync(path.join(dir, 'index.html'), page);
console.log(`built index.html (${(page.length / 1024).toFixed(0)} KB)`);

// the eagle drone viewer (one file: three.js from the CDN, the model inlined)
const vsrcPath = path.join(dir, 'eagle_viewer.src.html');
if (fs.existsSync(vsrcPath)) {
  const vsrc = fs.readFileSync(vsrcPath, 'utf8');
  if (!vsrc.includes('/*@@EAGLE@@*/')) throw new Error('eagle_viewer.src.html has no /*@@EAGLE@@*/ marker');
  const ref = 'data:image/jpeg;base64,' + fs.readFileSync(path.join(dir, '../../docs/images/eagle-drone-reference.jpg')).toString('base64');
  // still renders of the model, shown if the browser will not give the page a 3D view
  const still = 'data:image/jpeg;base64,' + fs.readFileSync(path.join(dir, '../../docs/images/eagle-drone-model.jpg')).toString('base64');
  if (!vsrc.includes('@@MODELIMG@@')) throw new Error('eagle_viewer.src.html has no @@MODELIMG@@ marker');
  // the cutaway view (inlined) and where its button points: the repo page opens itself at #cutaway in a new tab,
  // the published claude.ai page opens its own artifact link at #cutaway
  const cut = fs.readFileSync(path.join(dir, 'cutaway.js'), 'utf8');
  if (cut.includes('</script')) throw new Error('cutaway.js contains </script');
  new Function(cut);   // syntax check: a broken cutaway script would also stop the flight view from starting
  const ARTIFACT_URL = 'https://claude.ai/artifact/2YdCScNxSWJXALdtH1jRcE';
  const fill = (src, href) => src.replace('/*@@EAGLE@@*/', () => eagle).replace('/*@@CUTAWAY_JS@@*/', () => cut).replace('@@REF@@', () => ref).replace('@@MODELIMG@@', () => still).replace('@@CUTAWAY_HREF@@', () => href);
  for (const m of ['/*@@CUTAWAY_JS@@*/', '@@CUTAWAY_HREF@@']) if (!vsrc.includes(m)) throw new Error('eagle_viewer.src.html has no ' + m + ' marker');
  const vbody = fill(vsrc, '#cutaway'), abody = fill(vsrc, ARTIFACT_URL + '#cutaway');
  const viewer = '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n' +
    '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n</head>\n<body>\n' + vbody + '\n</body>\n</html>\n';
  fs.writeFileSync(path.join(dir, 'eagle_viewer.html'), viewer);
  if (process.env.VIEWER_BODY) fs.writeFileSync(process.env.VIEWER_BODY, abody);   // page body only, for publishing as a claude.ai artifact
  console.log(`built eagle_viewer.html (${(viewer.length / 1024).toFixed(0)} KB)`);
}
