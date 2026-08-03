const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const projectRoot = path.resolve(__dirname, '..');
const miniprogramRoot = path.join(projectRoot, 'miniprogram');
const expectedFloors = Array.from({ length: 13 }, (_, index) => `${index + 1}F.jpg`);

function walkFiles(directory) {
  return fs.readdirSync(directory, { withFileTypes: true })
    .flatMap(entry => {
      const fullPath = path.join(directory, entry.name);
      return entry.isDirectory() ? walkFiles(fullPath) : [fullPath];
    });
}

function readJpegSize(buffer) {
  assert.equal(buffer[0], 0xff);
  assert.equal(buffer[1], 0xd8);
  let offset = 2;
  while (offset + 8 < buffer.length) {
    if (buffer[offset] !== 0xff) {
      offset += 1;
      continue;
    }
    const marker = buffer[offset + 1];
    if ([0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf].includes(marker)) {
      return { height: buffer.readUInt16BE(offset + 5), width: buffer.readUInt16BE(offset + 7) };
    }
    if (marker === 0xd8 || marker === 0xd9 || marker === 0x01 || (marker >= 0xd0 && marker <= 0xd7)) {
      offset += 2;
      continue;
    }
    const length = buffer.readUInt16BE(offset + 2);
    offset += 2 + length;
  }
  throw new Error('JPEG dimensions were not found');
}

test('production code references only packaged floor-map assets', () => {
  const productionFiles = walkFiles(miniprogramRoot)
    .filter(file => ['.js', '.json', '.wxml'].includes(path.extname(file).toLowerCase()));
  const source = productionFiles.map(file => fs.readFileSync(file, 'utf8')).join('\n');

  const references = new Set(source.match(/\/assets\/[A-Za-z0-9_./-]+\.jpg/g) || []);
  assert.equal(references.size, 13);
  for (const reference of references) {
    const assetPath = path.join(miniprogramRoot, ...reference.slice(1).split('/'));
    assert.equal(fs.existsSync(assetPath), true, `missing packaged asset: ${reference}`);
  }
});

test('production package contains no local audio files or references', () => {
  const audioFiles = walkFiles(miniprogramRoot).filter(file =>
    /\.(?:mp3|wav|m4a|aac|ogg)$/i.test(file)
  );
  assert.deepEqual(audioFiles, []);

  const productionFiles = walkFiles(miniprogramRoot)
    .filter(file => ['.js', '.json', '.wxml', '.wxss'].includes(path.extname(file).toLowerCase()));
  const productionText = productionFiles.map(file => fs.readFileSync(file, 'utf8')).join('\n');
  assert.doesNotMatch(
    productionText,
    /assets\/audio|localFallback|playAudioArray|audioRoutes|getAudioForLeg/i
  );
});

test('all thirteen production floor maps are readable JPEGs within the release limits', () => {
  const directory = path.join(miniprogramRoot, 'assets/floor-maps');
  assert.deepEqual(fs.readdirSync(directory).sort(), expectedFloors.sort());
  for (const name of expectedFloors) {
    const file = path.join(directory, name);
    const buffer = fs.readFileSync(file);
    const size = readJpegSize(buffer);
    assert.ok(buffer.length <= 90 * 1024, `${name} exceeds 90 KiB`);
    assert.ok(size.width >= 800, `${name} is narrower than 800 pixels`);
  }
});
