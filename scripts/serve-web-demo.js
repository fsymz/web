#!/usr/bin/env node
'use strict';

const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');

const projectRoot = path.resolve(__dirname, '..');
const port = Number.parseInt(process.argv[2] || '41739', 10);
const host = '127.0.0.1';

const MIME_TYPES = Object.freeze({
  '.css': 'text/css; charset=utf-8',
  '.html': 'text/html; charset=utf-8',
  '.ico': 'image/x-icon',
  '.jpeg': 'image/jpeg',
  '.jpg': 'image/jpeg',
  '.js': 'text/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.png': 'image/png',
  '.svg': 'image/svg+xml'
});

function comparablePath(value) {
  const resolved = path.resolve(value);
  return process.platform === 'win32' ? resolved.toLowerCase() : resolved;
}

function isWithinRoot(candidate, root) {
  const normalizedCandidate = comparablePath(candidate);
  const normalizedRoot = comparablePath(root);
  return normalizedCandidate === normalizedRoot
    || normalizedCandidate.startsWith(normalizedRoot + path.sep);
}

function publicRootsFor(root) {
  return [
    path.join(root, 'web-demo'),
    path.join(root, 'miniprogram', 'assets')
  ];
}

function resolveRequestPath(rawUrl, root = projectRoot) {
  let pathname;
  try {
    pathname = decodeURIComponent(new URL(rawUrl || '/', `http://${host}`).pathname);
  } catch (_error) {
    return null;
  }
  const candidate = path.resolve(root, pathname.replace(/^\/+/, ''));
  if (!publicRootsFor(root).some(publicRoot => isWithinRoot(candidate, publicRoot))) {
    return null;
  }
  return candidate;
}

function sendText(response, statusCode, message) {
  const body = Buffer.from(message, 'utf8');
  response.writeHead(statusCode, {
    'cache-control': 'no-store',
    'content-length': body.length,
    'content-type': 'text/plain; charset=utf-8'
  });
  response.end(body);
}

function serveFile(request, response, filePath, stat) {
  response.writeHead(200, {
    'cache-control': 'no-store',
    'content-length': stat.size,
    'content-type': MIME_TYPES[path.extname(filePath).toLowerCase()] || 'application/octet-stream'
  });
  if (request.method === 'HEAD') {
    response.end();
    return;
  }
  const stream = fs.createReadStream(filePath);
  stream.on('error', () => {
    if (!response.headersSent) sendText(response, 500, 'Internal Server Error');
    else response.destroy();
  });
  stream.pipe(response);
}

function createWebDemoServer(root = projectRoot) {
  const realPublicRoots = publicRootsFor(root).map(publicRoot => fs.realpathSync(publicRoot));
  const server = http.createServer((request, response) => {
    if (!['GET', 'HEAD'].includes(request.method)) {
      sendText(response, 405, 'Method Not Allowed');
      return;
    }
    const resolved = resolveRequestPath(request.url, root);
    if (!resolved) {
      sendText(response, 400, 'Bad Request');
      return;
    }
    fs.stat(resolved, (initialError, initialStat) => {
      if (initialError) {
        sendText(response, 404, 'Not Found');
        return;
      }
      const filePath = initialStat.isDirectory() ? path.join(resolved, 'index.html') : resolved;
      fs.realpath(filePath, (realPathError, realFilePath) => {
        if (
          realPathError
          || !realPublicRoots.some(publicRoot => isWithinRoot(realFilePath, publicRoot))
        ) {
          sendText(response, realPathError ? 404 : 400, realPathError ? 'Not Found' : 'Bad Request');
          return;
        }
        fs.stat(realFilePath, (fileError, fileStat) => {
          if (fileError || !fileStat.isFile()) {
            sendText(response, 404, 'Not Found');
            return;
          }
          serveFile(request, response, realFilePath, fileStat);
        });
      });
    });
  });
  server.on('clientError', (_error, socket) => {
    socket.end('HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n');
  });
  return server;
}

if (require.main === module) {
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new Error(`Invalid port: ${process.argv[2] || ''}`);
  }
  const server = createWebDemoServer();
  server.on('error', error => {
    process.stderr.write(`Web demo server failed: ${error.message}\n`);
    process.exitCode = 1;
  });
  server.listen(port, host, () => {
    process.stdout.write(`Web demo server listening on http://${host}:${port}\n`);
  });
}

module.exports = {
  createWebDemoServer,
  resolveRequestPath
};
