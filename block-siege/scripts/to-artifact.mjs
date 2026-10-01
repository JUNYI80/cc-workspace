// Turns the single-file Vite build into a page body for sharing:
// drops the <html>/<head>/<body> wrappers, keeps <title>, font links and styles first,
// then the markup, then the inlined game script last.
import { readFileSync, writeFileSync } from 'node:fs';

const html = readFileSync('dist/index.html', 'utf8');
const head = html.match(/<head>([\s\S]*?)<\/head>/)[1];
const body = html.match(/<body>([\s\S]*?)<\/body>/)[1];

const title = head.match(/<title>[\s\S]*?<\/title>/)[0];
const links = head.match(/<link[^>]*>/g) ?? [];
const styles = head.match(/<style[^>]*>[\s\S]*?<\/style>/g) ?? [];
const scripts = [...(head.match(/<script[^>]*>[\s\S]*?<\/script>/g) ?? []), ...(body.match(/<script[^>]*>[\s\S]*?<\/script>/g) ?? [])];
const markup = body.replace(/<script[^>]*>[\s\S]*?<\/script>/g, '');

const out = [title, ...links, ...styles, markup.trim(), ...scripts].join('\n');
writeFileSync('dist/artifact.html', out);
console.log(`dist/artifact.html: ${(out.length / 1024).toFixed(0)} KB`);
