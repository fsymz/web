'use strict';

const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { locateProjectRoot } = require('./check-routes.js');

function walkFiles(directory) {
  const files = [];
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const absolute = path.join(directory, entry.name);
    if (entry.isDirectory()) files.push(...walkFiles(absolute));
    else if (entry.isFile()) files.push(absolute);
  }
  return files;
}

function checkWxml(source, fileName) {
  const errors = [];
  const clean = source.replace(/<!--[\s\S]*?-->/g, '');
  if (clean.includes('<!--') || clean.includes('-->')) {
    errors.push(`${fileName}: unbalanced WXML comment`);
  }
  const stack = [];
  const tagPattern = /<\s*(\/)?\s*([A-Za-z][\w-]*)([^<>]*?)(\/)?\s*>/g;
  let match;
  while ((match = tagPattern.exec(clean)) !== null) {
    const closing = Boolean(match[1]);
    const name = match[2];
    const selfClosing = Boolean(match[4]);
    if (closing) {
      const expected = stack.pop();
      if (expected !== name) {
        errors.push(`${fileName}: closing </${name}> does not match <${expected || 'none'}>`);
        break;
      }
    } else if (!selfClosing) {
      stack.push(name);
    }
  }
  if (stack.length) errors.push(`${fileName}: unclosed WXML tag <${stack[stack.length - 1]}>`);
  const withoutTags = clean.replace(tagPattern, '');
  if (/<\/?[A-Za-z]/.test(withoutTags)) errors.push(`${fileName}: malformed WXML tag`);
  return errors;
}

function checkBalancedCss(source, fileName) {
  const errors = [];
  let clean = source.replace(/\/\*[\s\S]*?\*\//g, '');
  if (clean.includes('/*') || clean.includes('*/')) {
    errors.push(`${fileName}: unbalanced WXSS comment`);
  }
  clean = clean.replace(/"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'/g, '');
  const pairs = { '{': '}', '[': ']', '(': ')' };
  const closing = new Set(Object.values(pairs));
  const stack = [];
  for (const char of clean) {
    if (pairs[char]) stack.push(pairs[char]);
    else if (closing.has(char) && stack.pop() !== char) {
      errors.push(`${fileName}: unbalanced WXSS delimiter ${char}`);
      break;
    }
  }
  if (stack.length) errors.push(`${fileName}: unclosed WXSS delimiter, expected ${stack.pop()}`);
  return errors;
}

function checkSyntax(projectRoot) {
  const miniprogram = path.join(projectRoot, 'miniprogram');
  const errors = [];
  const counts = { JavaScript: 0, JSON: 0, WXML: 0, WXSS: 0 };
  for (const file of walkFiles(miniprogram)) {
    const extension = path.extname(file).toLowerCase();
    const relative = path.relative(projectRoot, file);
    const source = fs.readFileSync(file, 'utf8');
    try {
      if (extension === '.js') {
        counts.JavaScript += 1;
        new vm.Script(source, { filename: relative });
      } else if (extension === '.json') {
        counts.JSON += 1;
        JSON.parse(source.replace(/^\uFEFF/, ''));
      } else if (extension === '.wxml') {
        counts.WXML += 1;
        errors.push(...checkWxml(source, relative));
      } else if (extension === '.wxss') {
        counts.WXSS += 1;
        errors.push(...checkBalancedCss(source, relative));
      }
    } catch (error) {
      errors.push(`${relative}: ${error.message}`);
    }
  }
  return { errors, counts };
}

function main() {
  const projectRoot = locateProjectRoot();
  if (!projectRoot) {
    console.error('Usage: node scripts/check-syntax.js');
    return 2;
  }
  try {
    const result = checkSyntax(projectRoot);
    console.log('Mini-program syntax verification');
    for (const [kind, count] of Object.entries(result.counts)) {
      console.log(`- ${kind}: ${count}`);
    }
    if (result.errors.length) {
      console.error(`Syntax verification failed with ${result.errors.length} error(s):`);
      result.errors.forEach(error => console.error(`- ${error}`));
      return 1;
    }
    console.log('PASS: JavaScript, JSON, WXML, and WXSS syntax checks passed.');
    return 0;
  } catch (error) {
    console.error(`Syntax verification failed: ${error.stack || error.message}`);
    return 1;
  }
}

if (require.main === module) process.exitCode = main();

module.exports = { checkBalancedCss, checkSyntax, checkWxml, main };
