/** Compatibility coverage for the parser resolution across Tailwind/CRA callers. */
const postcss = require('postcss');
const tailwind = require('tailwindcss');
const nested = require('postcss-nested');
const parser = require('postcss-selector-parser');
const { SourceMapConsumer, SourceMapGenerator } = require('source-map-js');

test('Tailwind group, peer, arbitrary and nested variants retain their selectors', async () => {
  const result = await postcss([tailwind({
    content: [{ raw: 'group-hover:block peer-checked:hidden [&>span]:flex hover:focus:block', extension: 'html' }],
    corePlugins: { preflight: false },
  })]).process('@tailwind utilities;', { from: undefined });
  expect(result.css).toContain('.group:hover');
  expect(result.css).toContain('.peer:checked ~');
  expect(result.css).toContain('>span');
  expect(result.css).toContain(':focus:hover');
  expect(result.css).not.toContain(':merge(');
});

test('nested selector interpolation retains ordinary CSS behavior', async () => {
  const result = await postcss([nested]).process('.a { & > .b, &:hover { color: red } }', { from: undefined });
  expect(result.css).toBe('.a > .b, .a:hover { color: red }');
});

test('wide flat selectors round-trip without losing nodes', () => {
  const selector = '.a'.repeat(20000);
  expect(parser().processSync(selector)).toBe(selector);
});

test.each([Infinity, -1, 0.5, 10000001])('indexed source maps reject unsafe line offset %s', line => {
  expect(() => new SourceMapConsumer({ version: 3, sections: [{
    offset: { line, column: 0 },
    map: { version: 3, sources: [], names: [], mappings: '' },
  }] })).toThrow(/offset/i);
});

test('ordinary source map generation and lookup remain valid', () => {
  const map = new SourceMapGenerator({ file: 'out.css' });
  map.addMapping({ generated: { line: 1, column: 0 }, original: { line: 2, column: 3 }, source: 'input.css' });
  const consumer = new SourceMapConsumer(map.toJSON());
  expect(consumer.originalPositionFor({ line: 1, column: 0 })).toMatchObject({ source: 'input.css', line: 2, column: 3 });
});
