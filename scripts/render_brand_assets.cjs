// Rebuild the supplied SVG as PNG and a Windows ICO. Requires the sharp package.
const fs = require('node:fs/promises');
const path = require('node:path');
const sharp = require('sharp');

async function main() {
  const root = path.resolve(__dirname, '..');
  const icons = path.join(root, 'src', 'codexio', 'icons');
  const branding = path.join(root, 'docs', 'branding', 'codexio');
  await fs.mkdir(branding, {recursive: true});
  const svg = await fs.readFile(path.join(icons, 'app-light.svg'));
  await fs.writeFile(path.join(icons, 'app.svg'), svg);
  const raster = size => sharp(svg, {density: 288}).resize(size, size).png().toBuffer();
  await fs.writeFile(path.join(icons, 'app.png'), await raster(1024));
  for (const name of ['app-light', 'app-dark', 'brand-mark']) {
    const source = await fs.readFile(path.join(icons, name + '.svg'));
    await fs.writeFile(path.join(icons, name + '.png'), await sharp(source, {density: 288}).resize(1024, 1024, {fit: 'contain', background: '#00000000'}).png().toBuffer());
  }
  for (const name of ['c-dot-ring-static', 'completed']) {
    const icon = path.join(icons, 'task-status', name);
    await fs.writeFile(icon + '.png', await sharp(await fs.readFile(icon + '.svg'), {density: 288}).resize(144, 144).png().toBuffer());
  }
  const sizes = [16, 24, 32, 48, 64, 128, 256];
  const payloads = await Promise.all(sizes.map(raster));
  const header = Buffer.alloc(6 + sizes.length * 16);
  header.writeUInt16LE(1, 2);
  header.writeUInt16LE(sizes.length, 4);
  let offset = header.length;
  sizes.forEach((size, index) => {
    const pos = 6 + index * 16;
    header[pos] = header[pos + 1] = size === 256 ? 0 : size;
    header.writeUInt16LE(1, pos + 4);
    header.writeUInt16LE(32, pos + 6);
    header.writeUInt32LE(payloads[index].length, pos + 8);
    header.writeUInt32LE(offset, pos + 12);
    offset += payloads[index].length;
  });
  await fs.writeFile(path.join(icons, 'app.ico'), Buffer.concat([header, ...payloads]));
  for (const [from, to] of [['app.svg', 'codexio-icon.svg'], ['app.png', 'codexio-icon.png'], ['app.ico', 'Codexio.ico'], ['app-light.svg', 'codexio-icon-light.svg'], ['app-dark.svg', 'codexio-icon-dark.svg'], ['app-light.png', 'codexio-icon-light.png'], ['app-dark.png', 'codexio-icon-dark.png']]) {
    await fs.copyFile(path.join(icons, from), path.join(branding, to));
  }
  console.log('Codexio light/dark SVG, 1024px PNG, and seven-size ICO generated.');
}
main().catch(error => { console.error(error.message); process.exitCode = 1; });
