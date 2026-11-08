// Builds index.html: inlines model.js and eagle_drone.js into index.src.html and wraps it in a standalone page.
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
const body = src.replace('/*@@MODEL@@*/', () => model).replace('/*@@EAGLE@@*/', () => eagle);
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
  const vbody = vsrc.replace('/*@@EAGLE@@*/', () => eagle).replace('@@REF@@', () => ref);
  const viewer = '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n' +
    '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n</head>\n<body>\n' + vbody + '\n</body>\n</html>\n';
  fs.writeFileSync(path.join(dir, 'eagle_viewer.html'), viewer);
  if (process.env.VIEWER_BODY) fs.writeFileSync(process.env.VIEWER_BODY, vbody);   // page body only, for publishing as a claude.ai artifact
  console.log(`built eagle_viewer.html (${(viewer.length / 1024).toFixed(0)} KB)`);
}
