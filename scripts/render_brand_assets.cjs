// Rebuild the supplied SVG as PNG and a Windows ICO. Requires the sharp package.
const fs = require('node:fs/promises');
const path = require('node:path');
const sharp = require('sharp');

async function renderWordmark(icons) {
  const source = await fs.readFile(path.join(icons, 'wordmark.svg'), 'utf8');
  const full = await sharp(Buffer.from(source), {density: 144}).resize({width: 1536}).png().toBuffer({resolveWithObject: true});
  const trimmed = await sharp(full.data).trim({threshold: 0}).png().toBuffer({resolveWithObject: true});
  // Remove only symmetric transparent margins: keep the original canvas centre.
  const left = Math.abs(trimmed.info.trimOffsetLeft || 0);
  const top = Math.abs(trimmed.info.trimOffsetTop || 0);
  const dx = Math.max(0, Math.min(left, full.info.width - left - trimmed.info.width) - 4);
  const dy = Math.max(0, Math.min(top, full.info.height - top - trimmed.info.height) - 4);
  await sharp(full.data).extract({left: dx, top: dy, width: full.info.width - 2 * dx, height: full.info.height - 2 * dy})
    .png().toFile(path.join(icons, 'wordmark.png'));
}

// Import vector art only: discard the presentation canvas outside the icon tile.
// Retain the supplied (627, 627) reference centre, never re-centre the glyph bounds.
async function importPack(pack, icons) {
  const source = await fs.readFile(path.join(pack, 'main/codexio-main.svg'), 'utf8');
  const paths = source.match(/<path\b[^>]*\/>/g) || [];
  if (paths.length !== 3 || !paths[2].includes('fill="#030303"')) throw new Error('Unexpected main logo vector');
  const glyph = paths[2].replace(/fill="[^"]+"/, 'fill="currentColor"');
  const scale = 512 / 976;
  for (const [name, background, foreground] of [['app-light', '#FFFFFF', '#000000'], ['app-dark', '#000000', '#FFFFFF']]) {
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" width="512" height="512"><title>Codexio</title><rect width="512" height="512" rx="112" fill="${background}"/><g color="${foreground}" transform="translate(256 256) scale(${scale}) translate(-627 -627)">${glyph}</g></svg>\n`;
    await fs.writeFile(path.join(icons, name + '.svg'), svg, 'utf8');
  }
  await fs.writeFile(path.join(icons, 'brand-mark.svg'), `<svg xmlns="http://www.w3.org/2000/svg" viewBox="24 24 464 464" width="464" height="464"><title>Codexio</title><g color="#000000" transform="translate(256 256) scale(.54) translate(-627 -627)">${glyph}</g></svg>\n`, 'utf8');
  const styles = path.join(icons, 'app-icons');
  await fs.mkdir(styles, {recursive: true});
  for (const name of (await fs.readdir(path.join(pack, 'variants'))).filter(name => name.endsWith('.svg')).sort()) {
    const source = await fs.readFile(path.join(pack, 'variants', name), 'utf8');
    const body = source.replace(/^[\s\S]*?<svg\b[^>]*>/, '').replace(/<\/svg>\s*$/, '');
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" width="512" height="512"><defs><clipPath id="codexio-tile"><rect width="512" height="512" rx="112"/></clipPath></defs><g clip-path="url(#codexio-tile)"><g transform="translate(256 256) scale(${scale}) translate(-627 -627)">${body}</g></g></svg>\n`;
    await fs.writeFile(path.join(styles, name), svg, 'utf8');
  }
}

async function main() {
  const root = path.resolve(__dirname, '..');
  const icons = path.join(root, 'src', 'codexio', 'icons');
  await renderWordmark(icons);
  if (process.argv.includes('--wordmark-only')) return;
  const branding = path.join(root, 'docs', 'branding', 'codexio');
  await fs.mkdir(branding, {recursive: true});
  const packArgument = process.argv.indexOf('--logo-pack');
  if (packArgument !== -1) {
    if (!process.argv[packArgument + 1]) throw new Error('--logo-pack requires a directory');
    await importPack(path.resolve(process.argv[packArgument + 1]), icons);
  }
  const svg = await fs.readFile(path.join(icons, 'app-light.svg'));
  await fs.writeFile(path.join(icons, 'app.svg'), svg);
  const raster = size => sharp(svg, {density: 288}).resize(size, size).png().toBuffer();
  await fs.writeFile(path.join(icons, 'app.png'), await raster(1024));
  for (const name of ['app-light', 'app-dark', 'brand-mark']) {
    const source = await fs.readFile(path.join(icons, name + '.svg'));
    await fs.writeFile(path.join(icons, name + '.png'), await sharp(source, {density: 288}).resize(1024, 1024, {fit: 'contain', background: '#00000000'}).png().toBuffer());
  }
  const styles = path.join(icons, 'app-icons');
  await fs.mkdir(styles, {recursive: true});
  for (const name of (await fs.readdir(styles)).filter(name => name.endsWith('.svg')).sort()) {
    const source = await fs.readFile(path.join(styles, name), 'utf8');
    const render = size => sharp(Buffer.from(source), {density: 144}).resize(size, size).png().toBuffer();
    await fs.writeFile(path.join(styles, name.replace('.svg', '.png')), await render(1024));
    await fs.writeFile(path.join(styles, name.replace('.svg', '-preview.png')), await render(160));
  }
  await fs.writeFile(path.join(styles, 'main-preview.png'), await sharp(svg).resize(160, 160).png().toBuffer());
  for (const name of packArgument === -1 ? ['c-dot-ring-static', 'completed'] : []) {
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
