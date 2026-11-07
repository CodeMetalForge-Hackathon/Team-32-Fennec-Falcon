// Builds index.html: inlines model.js into index.src.html and wraps it in a standalone page.
// Run after editing either file: node build.js
const fs = require('fs'), path = require('path');
const dir = __dirname;
const src = fs.readFileSync(path.join(dir, 'index.src.html'), 'utf8');
const model = fs.readFileSync(path.join(dir, 'model.js'), 'utf8');
if (model.includes('</script')) throw new Error('model.js contains </script');
if (!src.includes('/*@@MODEL@@*/')) throw new Error('index.src.html has no /*@@MODEL@@*/ marker');
const body = src.replace('/*@@MODEL@@*/', () => model);
const page = '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n' +
  '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n' +
  '<style>body{margin:0}[hidden]{display:none!important}</style>\n</head>\n<body>\n' +
  body + '\n</body>\n</html>\n';
fs.writeFileSync(path.join(dir, 'index.html'), page);
console.log(`built index.html (${(page.length / 1024).toFixed(0)} KB)`);
