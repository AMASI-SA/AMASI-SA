"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const braces = require("braces");
const { verifyCopy, verifyInstalled } = require("./verify-braces-backport.cjs");
const nested = (n, open = "{", close = "}") => open.repeat(n) + "a,b" + close.repeat(n);
const ast = n => {
  const root = { type: "root", nodes: [] };
  let parent = root;
  for (let i = 0; i < n; i++) {
    const child = { type: "brace", nodes: [], parent };
    parent.nodes.push(child);
    parent = child;
  }
  parent.nodes.push({ type: "text", value: "x", parent });
  return root;
};

test("every installed copy and transitive consumer uses reviewed patched bytes", () => assert.ok(verifyInstalled()));
test("tampering or a renamed unpatched copy fails integrity", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "braces-integrity-"));
  try {
    fs.cpSync(path.resolve(__dirname, "../vendor/braces"), dir, { recursive: true });
    fs.appendFileSync(path.join(dir, "lib/compile.js"), "\n// changed\n");
    assert.throws(() => verifyCopy(dir), /Backport integrity/);
  } finally { fs.rmSync(dir, { recursive: true, force: true }); }
});
for (const method of ["parse", "compile", "expand", "stringify"]) {
  for (const [open, close] of [["{", "}"], ["(", ")"]]) {
    test(`${method}: accepts 100 and bounds 101/4800 ${open} levels`, () => {
      assert.doesNotThrow(() => braces[method](nested(100, open, close)));
      for (const n of [101, 4800]) assert.throws(() => braces[method](nested(n, open, close)), /exceeds max depth/);
    });
  }
  test(`${method}: mixed nesting, fractional limit and raised limits cannot bypass`, () => {
    assert.throws(() => braces[method](nested(51, "{(", ")}")), /exceeds max depth/);
    assert.throws(() => braces[method]("(x)", { maxDepth: 0.5 }), /exceeds max depth/);
    for (const maxDepth of [10000, Infinity, NaN, "10000"]) {
      assert.throws(() => braces[method](nested(101), { maxDepth }), /exceeds max depth/);
    }
    assert.doesNotThrow(() => braces[method]("plain", { maxDepth: 0 }));
  });
}
for (const method of ["compile", "expand", "stringify"]) {
  test(`${method}: malformed AST values cannot reach recursive array coercion`, () => {
    let nestedValue = "x";
    for (let i = 0; i < 10000; i++) nestedValue = [nestedValue];
    const cyclicValue = []; cyclicValue.push(cyclicValue);
    for (const value of [nestedValue, cyclicValue, {}, 42, null]) {
      assert.throws(() => braces[method]({ type: "root", nodes: [{ type: "text", value }] }), {
        name: "TypeError", message: "AST value must be a string"
      });
    }
    for (const nodes of [42, {}, null]) {
      assert.throws(() => braces[method]({ type: "root", nodes }), /AST nodes must be an array/);
    }
    assert.throws(() => braces[method]({ type: "root", nodes: [null] }), /AST node must be an object/);
    assert.doesNotThrow(() => braces[method]({ type: "root", nodes: [{ type: "text", value: "x" }] }));
  });
  test(`${method}: caller AST depth and child cycles are bounded`, () => {
    assert.doesNotThrow(() => braces[method](ast(100)));
    assert.throws(() => braces[method](ast(30000)), /exceeds max depth/);
    const root = ast(0); root.nodes = [root];
    assert.throws(() => braces[method](root), /exceeds max depth/);
  });
}
test("cyclic AST parent chains terminate instead of hanging", () => {
  const parent = { type: "paren", nodes: [] }; parent.parent = parent;
  const root = { type: "root", nodes: [{ type: "paren", nodes: [], parent }] };
  assert.throws(() => vm.runInNewContext("expand(root)", { expand: braces.expand, root }, { timeout: 1000 }), /parent chain contains a cycle/);
});
test("parser's range-to-comma stringify path rejects excessive nesting", () => {
  assert.throws(() => braces.parse("{1.." + nested(101, "(", ")") + ",2}"), /exceeds max depth/);
});
test("escaping, quoted literals, expansion, ranges and ordinary option behavior remain", () => {
  assert.deepEqual(braces.expand("./src/**/*.{js,jsx,ts,tsx}"), ["./src/**/*.js", "./src/**/*.jsx", "./src/**/*.ts", "./src/**/*.tsx"]);
  assert.deepEqual(braces.expand("{a,b{1..3},c}"), ["a", "b1", "b2", "b3", "c"]);
  assert.deepEqual(braces.expand("{008..012..2}"), ["008", "010", "012"]);
  assert.deepEqual(braces.expand("{a,a,}", { nodupes: true, noempty: true }), ["a"]);
  assert.equal(braces.compile("src/*.{js,jsx}"), "src/*.(js|jsx)");
  assert.equal(braces.stringify("{a,b}", { escapeInvalid: true }), "{a,b}");
  assert.deepEqual(braces.expand("\\{a,b\\}", { keepEscaping: true }), ["\\{a,b\\}"]);
  assert.doesNotThrow(() => braces.parse('"' + nested(101) + '"'));
  assert.throws(() => braces.expand("{1..10000}"), /range limit/);
});
test("real glob consumers retain extension selection and reject malicious expansion", () => {
  const micromatch = require("micromatch");
  const glob = require("fast-glob");
  assert.deepEqual(micromatch(["a.js", "b.jsx", "c.css"], "*.{js,jsx}"), ["a.js", "b.jsx"]);
  const found = glob.sync("src/pages/PurchaseInvoices.{jsx,test.jsx}", { cwd: path.resolve(__dirname, "..") });
  assert.deepEqual(found.sort(), ["src/pages/PurchaseInvoices.jsx", "src/pages/PurchaseInvoices.test.jsx"]);
  assert.throws(() => micromatch.braceExpand(nested(4800)), /exceeds max depth/);
  assert.throws(() => glob.generateTasks(nested(4800)), /exceeds max depth/);
});
test("Tailwind3/PostCSS retain RTL, responsive, dark, apply and animation utilities", async () => {
  const postcss = require("postcss");
  const tailwind = require("tailwindcss");
  assert.match(require("tailwindcss/package.json").version, /^3\./);
  const config = require("../tailwind.config.js");
  const result = await postcss([tailwind({ ...config, content: [{ raw: '<div class="rtl:text-right md:flex dark:bg-background animate-in fade-in"></div>' }] })])
    .process("@tailwind utilities; .probe { @apply flex; }", { from: undefined });
  for (const marker of ['[dir="rtl"]', "768px", ".probe", "display: flex", "fade-in", "bg-background"]) assert.ok(result.css.includes(marker), marker);
});
test("chokidar3 brace glob still observes file additions and closes cleanly", { timeout: 15000 }, async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "braces-watch-"));
  const watcher = require("chokidar").watch(path.join(dir, "*.{js,jsx}").replace(/\\/g, "/"), { ignoreInitial: true });
  try {
    await new Promise((resolve, reject) => { watcher.once("ready", resolve); watcher.once("error", reject); });
    const observed = new Promise((resolve, reject) => { watcher.once("add", resolve); watcher.once("error", reject); });
    fs.writeFileSync(path.join(dir, "probe.jsx"), "fixture");
    assert.equal(path.basename(await observed), "probe.jsx");
  } finally { await watcher.close(); fs.rmSync(dir, { recursive: true, force: true }); }
});
