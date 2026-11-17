"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { createHash } = require("node:crypto");
const { spawnSync } = require("node:child_process");
const { createRequire } = require("node:module");
const { test } = require("node:test");
const { frontendRoot, variants, verifyCopy, verifyInstalled } = require("./verify-picomatch-backports.cjs");

const appRequire = createRequire(path.join(frontendRoot, "package.json"));
const modules = Object.keys(variants).map(variant => ({ variant,
  file: path.join(frontendRoot, "vendor", `picomatch-${variant}`, "index.js") }));
const deadline = 750;
const childProgram = `
  const { file, pattern, options, input, mode } = JSON.parse(process.argv[1]);
  const pm = require(file);
  let matches;
  if (mode === "remove-backslashes") matches = pm.removeBackslashes(pattern) === input;
  else if (mode === "scan-unescape") {
    const result = pm.scan(pattern, options);
    matches = result.glob === input && result.base === "" && result.prefix === "" && result.isGlob === true;
  }
  else if (mode === "makeRe") matches = pm.makeRe(pattern, options).test(input);
  else if (mode === "parse-compile") matches = pm.compileRe(pm.parse(pattern, options), options).test(input);
  else matches = pm(pattern, options)(input);
  process.stdout.write(JSON.stringify({ matches }));
`;

function probe(file, pattern, input, options = {}, mode = "matcher") {
  // A JavaScript timer cannot interrupt a blocked regexp. The supervising
  // process kills this isolated child, including while native regexp work runs.
  const result = spawnSync(process.execPath, ["-e", childProgram,
    JSON.stringify({ file, pattern, input, options, mode })], {
    encoding: "utf8", timeout: deadline, killSignal: "SIGKILL", maxBuffer: 64 * 1024,
  });
  assert.ifError(result.error && new Error(`Glob child failed within ${deadline} ms: ${result.error.code}`));
  assert.equal(result.signal, null, `Glob child terminated: ${result.signal}`);
  assert.equal(result.status, 0, `Glob child failed: ${String(result.stderr).slice(0, 1200)}`);
  const output = JSON.parse(result.stdout);
  assert.equal(typeof output.matches, "boolean");
  return output.matches;
}

const hostile = "a".repeat(64) + "!";
const attacks = [
  ["wildcard-suffix", "+(*a)", hostile],
  ["nested-at-wildcard", "+(@(*a))", hostile],
  ["nested-at-overlap", "+(@(a|aa))", hostile],
  ["raw-quantifier", "+((a+))", hostile],
  ["raw-wrapper-around-extglob", "(+(a))+", hostile],
  ["raw-wrapper-around-alternation", "(+(a|b))+", hostile],
  ["raw-wrapper-star-option", "(+(a))*", hostile, { regex: true }],
  ["raw-wrapper-opt-out", "(+(a))+", hostile, { maxExtglobRecursion: false }],
  ["raw-nested-quantifier", "(a+)+", hostile],
  ["single-star", "+(*)", "a".repeat(64) + "/"],
  ["overlapping-alternatives", "+(a|aa)", hostile],
  ["repeated-word-prefix", "+(ab|abab)", "ab".repeat(32) + "!"],
  ["star-with-alternative", "+(*a|b)", hostile],
  ["nested-optional", "+(?(a))", hostile],
  ["recursion-opt-out", "+(@(*a))", hostile, { maxExtglobRecursion: false }],
  ["raised-recursion-limit", "+(@(a|aa))", hostile, { maxExtglobRecursion: 1000000 }],
  ["disabled-fastpaths", "+(*a)", hostile, { fastpaths: false }],
  ["capture-option", "+(*a)", hostile, { capture: true }],
  ["bracket-disabled-wildcard", "+([*])", "[a]", { nobracket: true }],
  ["path-context", "src/+(*a).js", "src/" + "a".repeat(64) + "!.js"],
];

// Frozen from the unchanged picomatch 2.3.2 and 4.0.7 release utilities.
// In particular, one line terminator immediately before ] may be the final
// character of a preserved bracket span; a line terminator inside it may not.
const unescapeFixtures = [
  ["", ""], ["[", "["], ["[]", "[]"], ["[]]", "[]]"], ["[a]", "[a]"],
  ["[\\]", "[]"], ["[\\a]", "[\\a]"], ["[a\\]]", "[a\\]]"],
  ["[[\\a]", "[[\\a]"], ["[\\a", "[a"], ["\\[a\\]", "[a]"], ["\\[a]", "[a]"],
  ["\\\\", "\\"], ["\\", "\\"], ["\\a", "a"], ["\\\\a", "a"], ["a\\b", "ab"],
  ["[\\a][\\b]", "[\\a][\\b]"],
  ["[a\\b\n]", "[a\\b\n]"], ["[a\\b\r]", "[a\\b\r]"],
  ["[a\\b\u2028]", "[a\\b\u2028]"], ["[a\\b\u2029]", "[a\\b\u2029]"],
  ["[a\\b\r\nc]", "[ab\r\nc]"], ["[a\\b\nc]", "[ab\nc]"],
  ["\\\n", "\\\n"], ["\\\r", "\\\r"], ["\\\u2028", "\\\u2028"], ["\\\u2029", "\\\u2029"],
  ["[\\\n]", "[\\\n]"], ["[a\\\nb]", "[a\\\nb]"],
  ["[\\a\\]]", "[\\a\\]]"], ["a\\b[\\c]d\\e", "ab[\\c]de"],
];
const unescapeAlphabet = ["[", "]", "\\", "a", "\n", "\r", "\u2028", "\u2029"];
function* boundedUnescapeInputs(prefix = "") {
  yield prefix;
  if (prefix.length < 5) {
    for (const character of unescapeAlphabet) yield* boundedUnescapeInputs(prefix + character);
  }
}

for (const { variant, file } of modules) {
  test(`${variant}: removeBackslashes unescaping remains bounded for unmatched brackets`, () => {
    const utility = path.join(path.dirname(file), "lib/utils.js");
    const brackets = "[".repeat(60000);
    // Exercise actual unescaping first, then the no-backslash fast path.
    for (const [suffix, expected] of [["\\a", "a"], ["", ""]]) {
      assert.equal(probe(utility, brackets + suffix, brackets + expected, {}, "remove-backslashes"), true);
    }
  });
  test(`${variant}: public scan unescaping remains bounded for unmatched brackets`, () => {
    const brackets = "*" + "[".repeat(60000);
    for (const [suffix, expected] of [["\\a", "a"], ["", ""]]) {
      assert.equal(probe(file, brackets + suffix, brackets + expected, { unescape: true }, "scan-unescape"), true);
    }
  });
  test(`${variant}: bracket and line-break unescaping preserves exact upstream semantics`, () => {
    const { removeBackslashes } = require(path.join(path.dirname(file), "lib/utils.js"));
    for (const [input, expected] of unescapeFixtures) {
      assert.equal(removeBackslashes(input), expected, JSON.stringify(input));
    }
    // The digest records all 37,449 inputs of at most five code units in this
    // alphabet, in generator order. Both unchanged official releases agree.
    // Keeping their bounded oracle as data avoids embedding the unsafe regex.
    const oracle = createHash("sha256");
    let count = 0;
    for (const input of boundedUnescapeInputs()) {
      oracle.update(JSON.stringify([input, removeBackslashes(input)]) + "\n");
      count++;
    }
    assert.equal(count, 37449);
    assert.equal(oracle.digest("hex"), "01ff47a1f7988706b46827ef00b010ad498ff477558ab0944bffdde5fe67b239");
  });
  test(`${variant}: public scan unescaping preserves bracket and line-break state`, () => {
    const pm = require(file);
    const examples = [
      ["src/\\*.js", "src/*.js", "", false, false],
      ["src/[a\\]b]/*.js", "src", "[a\\]b]/*.js", true, true],
      ["src/\\[a]/file\\?.js", "src/[a]/file?.js", "", false, false],
      ["src/[a\\b\n]/*.js", "src", "[a\\b\n]/*.js", true, true],
      ["src/[a\\b\nc]/*.js", "src", "[ab\nc]/*.js", true, true],
      ["src/[a\\b\r\nc]/*.js", "src", "[ab\r\nc]/*.js", true, true],
      ["src/[\\\n]/*.js", "src", "[\\\n]/*.js", true, true],
      ["src/a\\\\b/*.js", "src/ab", "*.js", true, false],
      ["*[\\]", "", "*[]", true, false],
      ["*[a\\]]", "", "*[a\\]]", true, false],
      ["*[a\\b\u2028]", "", "*[a\\b\u2028]", true, false],
      ["*[a\\b\u2029c]", "", "*[ab\u2029c]", true, false],
    ];
    for (const [input, base, glob, isGlob, isBracket] of examples) {
      const state = pm.scan(input, { unescape: true });
      assert.deepEqual({ base: state.base, glob: state.glob, prefix: state.prefix,
        isGlob: state.isGlob, isBracket: state.isBracket },
      { base, glob, prefix: "", isGlob, isBracket }, JSON.stringify(input));
    }
  });
  for (const [name, pattern, input, options] of attacks) {
    for (const mode of ["matcher", "makeRe", "parse-compile"]) {
      test(`adversarial ${variant}: ${name} [${mode}]`, () => {
        assert.equal(probe(file, pattern, input, options, mode), false);
      });
    }
  }
  test(`${variant}: pattern arrays and ignore globs cannot bypass the guard`, () => {
    assert.equal(probe(file, ["*.js", "+(*a)"], hostile), false);
    assert.equal(probe(file, "*", hostile, { ignore: "+(*a)" }), true);
  });
  test(`${variant}: unsafe repeated glob is literalized, not silently broadened`, () => {
    const pm = require(file);
    for (const pattern of ["+(*a)", "+(@(*a))", "+(@(a|aa))", "+((a+))"]) {
      assert.equal(pm(pattern)(pattern), true, pattern);
    }
  });
  test(`${variant}: Unicode surrogate union cannot introduce an emoji match`, () => {
    const pm = require(file);
    const { analyzeCompiledRepeat } = require(path.join(path.dirname(file), "lib/repeated-extglob-safety.js"));
    const body = String.raw`(?:\uD83D)*|(?:\uDE00)*`;
    const emoji = "\u{1F600}";
    // These independent regexes demonstrate the language difference: separate
    // lone-surrogate alternatives cannot consume a Unicode code point, whereas
    // adjoining their escapes in one class can fuse them into that code point.
    for (const flags of ["u", "v"]) {
      assert.equal(new RegExp(`^(?:(?:${body})+)$`, flags).test(emoji), false);
      assert.equal(new RegExp(String.raw`^[\uD83D\uDE00]*$`, flags).test(emoji), true);
      assert.deepEqual(analyzeCompiledRepeat(body, { flags }), { risky: true });
      const pattern = "x+(*(\uD83D)|*(\uDE00))y";
      assert.equal(pm(pattern, { flags })(`x${emoji}y`), false);
      assert.equal(pm(pattern, { flags })(pattern), true, "Unsafe union remains literal text");
    }
  });
  test(`${variant}: alternatives duplicated after case folding cannot bypass repeat protection`, () => {
    const pm = require(file);
    const { analyzeCompiledRepeat } = require(path.join(path.dirname(file), "lib/repeated-extglob-safety.js"));
    for (const options of [{ nocase: true }, { flags: "i" }, { flags: "iu" }, { flags: "iv" }]) {
      assert.deepEqual(analyzeCompiledRepeat("a|A", options), { risky: true });
      assert.equal(probe(file, "+(a|A)", hostile, options), false);
      assert.equal(pm("+(a|A)", options)("+(a|A)"), true);
    }
  });
  test(`${variant}: unescape and nobracket options are checked after their effective meaning changes`, () => {
    const pm = require(file);
    assert.equal(pm(String.raw`+(\*)`)("***"), true, "An ordinary escaped star remains literal");
    assert.equal(pm("+([*])")("***"), true, "An ordinary bracket star remains literal");
    for (const [pattern, options, input] of [
      [String.raw`+(\*)`, { unescape: true }, "aaa"],
      ["+([*])", { nobracket: true }, "[a]"],
      ["+([*])", { nobracket: true, unescape: true, maxExtglobRecursion: false }, "[a]"],
    ]) {
      assert.equal(probe(file, pattern, input, options), false);
      assert.equal(pm(pattern, options)(pattern), true, "Option-dependent unsafe body is literalized");
    }
  });
  test(`${variant}: escaped, quoted and bracket-literal stars preserve their meaning`, () => {
    const pm = require(file);
    for (const pattern of [String.raw`+(\*)`, '+("*")', "+([*])"]) {
      assert.equal(pm(pattern)("***"), true, pattern);
      assert.equal(pm(pattern)("aaa"), false, pattern);
    }
    assert.equal(pm('+("*a")')("*a*a"), true);
    assert.equal(pm(String.raw`\+\(\*a\)`)("+(*a)"), true);
    assert.equal(pm('"literal*name"')("literal*name"), true);
  });
  test(`${variant}: ordinary glob, extglob and consumer patterns retain semantics`, () => {
    const pm = require(file);
    assert.equal(pm('(+(a))+', { regex: false })('aaa+'), true);
    assert.equal(pm('(+(a))+(b)')('aaabbb'), true);
    assert.equal(pm('(+(a))*(b)', { regex: true })('aaabbb'), true);
    const examples = [
      ["+(test|spec)", ["test", "spec", "testspecspec"], ["tes", "testx"]],
      ["(ab)+", ["ab", "abab"], ["aba"]],
      ["(?:ab)+", ["ab", "abab"], ["aba"]],
      ["**/?(*.)+(spec|test).[tj]s?(x)", ["src/widget.spec.jsx", "test.ts", "lib/a.test.js"], ["src/widget.js"]],
      ["foo/bar/**/*.+(js|jsx)", ["foo/bar/a.js", "foo/bar/a/b.jsx"], ["foo/bar/a.css"]],
      ["+(a?)", ["ab", "abac"], ["a", "aba"]],
      ["+(@(a|b|c))", ["a", "abccab"], ["abd", ""]],
      ["+([0-7])", ["0", "012776"], ["8", ""]],
      ["*(*(f)*(o))", ["ffoff", "ofo"], ["", "bar"]],
      ["x*(*(f)*(o))y", ["xy", "xffoy"], ["xay"]],
      ["+(*)c", ["c", "abc", "aaac"], ["abcd"]],
      ["src/**/*.{js,jsx}", ["src/a.js", "src/nested/b.jsx"], ["src/a.css"]],
    ];
    for (const [pattern, matches, misses] of examples) {
      const match = pm(pattern);
      for (const input of matches) assert.equal(match(input), true, `${pattern} must match ${input}`);
      for (const input of misses) assert.equal(match(input), false, `${pattern} must not match ${input}`);
    }
    assert.equal(pm("**/*.js")("src/.hidden.js"), false);
    assert.equal(pm("**/*.js", { dot: true })("src/.hidden.js"), true);
    assert.equal(pm("**/*.js", { windows: true })(String.raw`src\nested\file.js`), true);
    assert.equal(pm("*.JS", { nocase: true })("file.js"), true);
    assert.ok(pm.scan("src/**/*.{js,jsx}").isGlob);
    const matcher = pm("*.js", {}, true);
    assert.ok(matcher.state && typeof matcher.state.output === "string");
    assert.ok(pm.makeRe("+(test|spec)", { capture: true }).exec("testspec"));
  });
  test(`${variant}: a flattened lazy inner star retains greedy outer capture priority`, () => {
    const { analyzeCompiledRepeat } = require(path.join(path.dirname(file), 'lib/repeated-extglob-safety.js'));
    const result = analyzeCompiledRepeat('a*?');
    assert.equal(result.safeOutput, 'a*');
    const fixed = new RegExp(`^((?:${result.safeOutput}))(a*)$`);
    // Freeze the original greedy-outer result without constructing its unsafe
    // nested repetition solely to obtain these three known captures.
    assert.deepEqual(Array.from(fixed.exec('aaa')), ['aaa', 'aaa', '']);
  });
}

test("v4: safe star alternatives preserve every branch and empty inner consumption", () => {
  const pm = require(modules.find(item => item.variant === "v4").file);
  for (const input of ["a", "b", "abba"]) assert.equal(pm("+(*(a)|*(b))")(input), true, input);
  assert.equal(pm("+(*(a)|*(b))")(""), false, "The public matcher rejects an empty input string");
  assert.equal(pm("x+(*(a)|*(b))y")("xy"), true, "The inner group can consume zero characters");
  assert.equal(pm("+(*(a)|*(b))")("c"), false);
  const posix = require(path.join(path.dirname(modules.find(item => item.variant === "v4").file), "posix.js"));
  assert.equal(posix("src/**/*.js")("src/a.js"), true);
});

test("v4: the public POSIX entrypoint applies the same repeated-extglob protection", () => {
  const file = path.join(path.dirname(modules.find(item => item.variant === "v4").file), "posix.js");
  assert.equal(probe(file, "+(*a)", hostile), false);
});

test("all installed picomatch copies and every declarative consumer use the correct reviewed major", () => {
  const result = verifyInstalled();
  assert.ok(result.copies.length >= 2);
  assert.ok(result.consumers.length >= 5);
});

function withCopy(variant, action) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "picomatch-integrity-"));
  try {
    fs.cpSync(path.join(frontendRoot, "vendor", `picomatch-${variant}`), directory, { recursive: true });
    return action(directory);
  } finally { fs.rmSync(directory, { recursive: true, force: true }); }
}

for (const variant of Object.keys(variants)) {
  test(`${variant}: changed runtime bytes, metadata and provenance are rejected`, () => {
    for (const file of ["lib/parse.js", "package.json", "SECURITY-PROVENANCE.json"]) {
      withCopy(variant, directory => {
        fs.appendFileSync(path.join(directory, file), "\n ");
        assert.throws(() => verifyCopy(directory, variant), /integrity|provenance/i);
      });
    }
  });
  test(`${variant}: a leftover upstream identity or missing runtime file is rejected`, () => {
    withCopy(variant, directory => {
      const file = path.join(directory, "package.json");
      const pkg = JSON.parse(fs.readFileSync(file));
      pkg.name = "picomatch";
      pkg.version = variant === "v2" ? "2.3.2" : "4.0.7";
      fs.writeFileSync(file, JSON.stringify(pkg));
      assert.throws(() => verifyCopy(directory, variant), /identity/);
    });
    withCopy(variant, directory => {
      fs.unlinkSync(path.join(directory, "lib/parse.js"));
      assert.throws(() => verifyCopy(directory, variant), /inventory|ENOENT/);
    });
  });
  test(`${variant}: additional unbound runtime bytes and symlinks are rejected`, () => {
    withCopy(variant, directory => {
      fs.writeFileSync(path.join(directory, "lib/unreviewed.js"), "module.exports = true;\n");
      assert.throws(() => verifyCopy(directory, variant), /inventory/);
    });
    withCopy(variant, directory => {
      fs.unlinkSync(path.join(directory, "lib/parse.js"));
      fs.symlinkSync(path.join(directory, "lib/scan.js"), path.join(directory, "lib/parse.js"));
      assert.throws(() => verifyCopy(directory, variant), /symlink/);
    });
  });
}

function withInstalledFixture(action) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "picomatch-installed-"));
  try {
    for (const variant of Object.keys(variants)) {
      fs.cpSync(path.join(frontendRoot, "vendor", `picomatch-${variant}`),
        path.join(root, "vendor", `picomatch-${variant}`), { recursive: true });
    }
    const copies = [
      ["v2", "node_modules/picomatch"],
      ["v4", "node_modules/vite/node_modules/picomatch"],
      ["v4", "node_modules/tinyglobby/node_modules/picomatch"],
    ];
    for (const [variant, relative] of copies) {
      fs.cpSync(path.join(root, "vendor", `picomatch-${variant}`), path.join(root, relative), { recursive: true });
    }
    for (const [name, major] of [["micromatch", 2], ["anymatch", 2], ["readdirp", 2], ["vite", 4], ["tinyglobby", 4]]) {
      const directory = path.join(root, "node_modules", name);
      fs.mkdirSync(directory, { recursive: true });
      fs.writeFileSync(path.join(directory, "package.json"), JSON.stringify({ name, version: "1.0.0",
        dependencies: { picomatch: `^${major}.0.0` } }));
    }
    verifyInstalled({ root });
    return action(root);
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
}

test("an unused nested upstream copy is rejected even when normal consumers remain patched", () => {
  withInstalledFixture(root => {
    const nested = path.join(root, "node_modules/unused/node_modules/picomatch");
    fs.cpSync(path.join(root, "vendor/picomatch-v2"), nested, { recursive: true });
    const file = path.join(nested, "package.json");
    const pkg = JSON.parse(fs.readFileSync(file));
    pkg.name = "picomatch"; pkg.version = "2.3.2";
    fs.writeFileSync(file, JSON.stringify(pkg));
    assert.throws(() => verifyInstalled({ root }), /Unreviewed picomatch/);
  });
});

test("consumer resolution rejects a patched package from the wrong major", () => {
  withInstalledFixture(root => {
    const target = path.join(root, "node_modules/vite/node_modules/picomatch");
    fs.rmSync(target, { recursive: true });
    fs.cpSync(path.join(root, "vendor/picomatch-v2"), target, { recursive: true });
    assert.throws(() => verifyInstalled({ root }), /identity/);
  });
});

test("omitting a runtime file from the reviewed hash manifest fails closed", () => {
  withInstalledFixture(root => {
    for (const relative of ["vendor/picomatch-v2", "node_modules/picomatch"]) {
      const file = path.join(root, relative, "SECURITY-PROVENANCE.json");
      const provenance = JSON.parse(fs.readFileSync(file));
      delete provenance.patched_files_sha256["lib/scan.js"];
      fs.writeFileSync(file, JSON.stringify(provenance));
    }
    assert.throws(() => verifyInstalled({ root }), /inventory/);
  });
});

test("real micromatch, fast-glob, tinyglobby, anymatch and Rollup filters preserve source selection", async () => {
  const files = ["src/a.js", "src/b.jsx", "src/a.css", "src/nested/a.test.tsx"];
  const micromatch = appRequire("micromatch");
  assert.deepEqual(micromatch(files, "**/*.{js,jsx}"), ["src/a.js", "src/b.jsx"]);
  assert.deepEqual(micromatch(files, "**/?(*.)+(spec|test).[tj]s?(x)"), ["src/nested/a.test.tsx"]);
  const expected = ["src/pages/PurchaseInvoices.jsx", "src/pages/PurchaseInvoices.test.jsx"];
  const pattern = "src/pages/PurchaseInvoices.{jsx,test.jsx}";
  assert.deepEqual(appRequire("fast-glob").sync(pattern, { cwd: frontendRoot }).sort(), expected);
  assert.deepEqual((await appRequire("tinyglobby").glob(pattern, { cwd: frontendRoot })).sort(), expected);
  const anymatch = appRequire("anymatch");
  assert.equal(anymatch(["**/*.js", "!**/ignored.js"], "src/a.js"), true);
  assert.equal(anymatch(["**/*.js", "!**/ignored.js"], "src/ignored.js"), false);
  const { createFilter } = appRequire("@rollup/pluginutils");
  const filter = createFilter("**/*.+(js|jsx)", "**/*.test.jsx", { resolve: frontendRoot });
  assert.equal(filter(path.join(frontendRoot, "src/a.jsx")), true);
  assert.equal(filter(path.join(frontendRoot, "src/a.test.jsx")), false);
  const unrootedFilter = createFilter("**/*.+(js|jsx)", "**/*.test.jsx", { resolve: false });
  assert.equal(unrootedFilter("/src/a.jsx"), true);
  assert.equal(unrootedFilter("/src/a.test.jsx"), false);
});

test("both real Jest consumers preserve matcher state and positive/negative test globs", () => {
  const watcher = createRequire(appRequire.resolve("jest-watcher/package.json"));
  for (const load of [appRequire, watcher]) {
    const { globsToMatcher } = load("jest-util");
    const match = globsToMatcher(["**/?(*.)+(spec|test).[tj]s?(x)", "!**/ignored.test.js"]);
    assert.equal(match("src/example.test.jsx"), true);
    assert.equal(match("src/ignored.test.js"), false);
    assert.equal(match("src/example.js"), false);
  }
});

test("real readdirp filters and chokidar events retain glob semantics and close cleanly", { timeout: 15000 }, async () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "picomatch-consumers-"));
  let watcher;
  try {
    fs.writeFileSync(path.join(directory, "keep.js"), "fixture");
    fs.writeFileSync(path.join(directory, "ignore.css"), "fixture");
    const found = await appRequire("readdirp").promise(directory, { fileFilter: ["*.js", "!ignored.js"] });
    assert.deepEqual(found.map(entry => entry.basename), ["keep.js"]);
    watcher = appRequire("chokidar").watch(path.join(directory, "*.{js,jsx}"), { ignoreInitial: true });
    await new Promise((resolve, reject) => { watcher.once("ready", resolve); watcher.once("error", reject); });
    const observed = new Promise((resolve, reject) => { watcher.once("add", resolve); watcher.once("error", reject); });
    fs.writeFileSync(path.join(directory, "new.jsx"), "fixture");
    assert.equal(path.basename(await observed), "new.jsx");
  } finally {
    if (watcher) await watcher.close();
    fs.rmSync(directory, { recursive: true, force: true });
  }
});

test("Tailwind 3 and PostCSS preserve RTL, responsive, dark and apply output", async () => {
  const postcss = appRequire("postcss");
  const tailwind = appRequire("tailwindcss");
  const config = appRequire("./tailwind.config.js");
  assert.match(appRequire("tailwindcss/package.json").version, /^3\./);
  const output = await postcss([tailwind({ ...config,
    content: [{ raw: '<div class="rtl:text-right md:flex dark:bg-background animate-in fade-in"></div>' }],
  })]).process("@tailwind utilities; .probe { @apply flex; }", { from: undefined });
  for (const marker of ['[dir="rtl"]', "768px", ".probe", "display: flex", "fade-in", "bg-background"]) {
    assert.ok(output.css.includes(marker), marker);
  }
});
