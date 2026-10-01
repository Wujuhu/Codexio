// Small Windows tray assets, derived from the approved transparent brand SVG.
// Uses the same sharp + PNG-in-ICO mechanism as render_brand_assets.cjs.
const fs = require('node:fs/promises');
const path = require('node:path');
const sharp = require('sharp');

async function main() {
  const root = path.resolve(__dirname, '..');
  const brand = path.join(root, 'windows/frontend/public/brand');
  const source = await fs.readFile(path.join(root, 'src/codexio/icons/brand-mark.svg'), 'utf8');
  // Keep the source viewBox and its reference centre, including transparent margins.
  await fs.writeFile(path.join(brand, 'brand-mark.svg'), source, 'utf8');
  const sizes = [16, 20, 24, 28, 32, 40, 48, 64];
  for (const [theme, color] of [['light', '#000000'], ['dark', '#FFFFFF']]) {
    const svg = Buffer.from(source.replace('color="#000000"', `color="${color}"`));
    const images = await Promise.all(sizes.map(size => sharp(svg, { density: 288 })
      .resize(size, size, { kernel: 'lanczos3' }).png().toBuffer()));
    const header = Buffer.alloc(6 + sizes.length * 16);
    header.writeUInt16LE(1, 2);
    header.writeUInt16LE(sizes.length, 4);
    let offset = header.length;
    sizes.forEach((size, index) => {
      const pos = 6 + index * 16;
      header[pos] = header[pos + 1] = size;
      header.writeUInt16LE(1, pos + 4);
      header.writeUInt16LE(32, pos + 6);
      header.writeUInt32LE(images[index].length, pos + 8);
      header.writeUInt32LE(offset, pos + 12);
      offset += images[index].length;
    });
    await fs.writeFile(path.join(brand, `tray-${theme}.ico`), Buffer.concat([header, ...images]));
  }
  console.log('Windows transparent SVG and light/dark tray ICOs generated (16–64 px).');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
