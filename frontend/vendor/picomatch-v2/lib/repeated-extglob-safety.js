'use strict';

// This is a deliberately restricted parser for an already-compiled extglob body.
// It does not parse glob syntax or try to recognize every safe regular expression.
// Unsupported constructs are literalized by the caller, including when the old
// maxExtglobRecursion option disables the upstream recursion heuristic.
const MAX_SOURCE_LENGTH = 8192;
const MAX_DEPTH = 32;
const MAX_WORDS = 64;
const MAX_EXPANDED_LENGTH = 8192;
const LITERAL_ESCAPES = '^$\\.*+?()[]{}|/';
const CHARACTER_ESCAPES = 'dDsSwW';
const CONTROL_ESCAPES = { f: '\f', n: '\n', r: '\r', t: '\t', v: '\v' };

const risky = () => ({ risky: true });

const parseCompiledBody = (source, flags) => {
  let index = 0;
  let failed = false;
  const unicode = flags.includes('u') || flags.includes('v');

  const escape = inClass => {
    const start = index++;
    const value = source[index++];
    if (value === undefined) return;

    if (LITERAL_ESCAPES.includes(value) || (value === '-' && (inClass || !unicode))) {
      return { kind: 'atom', literal: value, source: source.slice(start, index) };
    }
    if (Object.prototype.hasOwnProperty.call(CONTROL_ESCAPES, value)) {
      return { kind: 'atom', literal: CONTROL_ESCAPES[value], source: source.slice(start, index) };
    }
    if (inClass && value === 'b') {
      return { kind: 'atom', literal: '\b', source: source.slice(start, index) };
    }
    if (CHARACTER_ESCAPES.includes(value)) {
      return { kind: 'atom', literal: null, source: source.slice(start, index) };
    }
    if (value === 'x' || value === 'u') {
      const length = value === 'x' ? 2 : 4;
      const digits = source.slice(index, index + length);
      if (digits.length !== length || !/^[\da-f]+$/i.test(digits)) return;
      index += length;
      return {
        kind: 'atom',
        literal: String.fromCharCode(parseInt(digits, 16)),
        source: source.slice(start, index)
      };
    }
    // In particular: no assertions, backreferences, property/string escapes,
    // octal escapes, control-letter escapes or option-dependent identity escapes.
  };

  const characterClass = () => {
    const start = index++;
    // Unicode-set classes can contain strings and nested set expressions. They
    // are outside this single-character grammar, even if some are harmless.
    if (flags.includes('v')) return;
    if (source[index] === '^') index++;
    while (index < source.length) {
      if (source[index] === ']') {
        index++;
        const atomSource = source.slice(start, index);
        try {
          // This contains exactly one scanned class, never arbitrary regex code.
          // Validate ranges and the effective Unicode escape rules as well.
          new RegExp(atomSource, flags);
        } catch {
          return;
        }
        return { kind: 'atom', literal: null, source: atomSource };
      }
      if (source[index] === '\\') {
        if (!escape(true)) return;
      } else {
        index++;
      }
    }
  };

  const expression = depth => {
    if (depth > MAX_DEPTH) {
      failed = true;
      return;
    }
    const branches = [];
    while (!failed) {
      const items = [];
      while (index < source.length && source[index] !== '|' && source[index] !== ')') {
        let atom;
        const start = index;
        const value = source[index];
        if (value === '(') {
          index++;
          let capture = true;
          if (source[index] === '?') {
            if (source.slice(index, index + 2) !== '?:') {
              failed = true;
              return;
            }
            index += 2;
            capture = false;
          }
          const child = expression(depth + 1);
          if (failed || source[index] !== ')') {
            failed = true;
            return;
          }
          index++;
          atom = { kind: 'group', child, capture, source: source.slice(start, index) };
        } else if (value === '[') {
          atom = characterClass();
        } else if (value === '\\') {
          atom = escape(false);
        } else if (value === '.') {
          index++;
          atom = { kind: 'atom', literal: null, source: value };
        } else if ('^$*+?{}]'.includes(value)) {
          failed = true;
          return;
        } else {
          index++;
          atom = { kind: 'atom', literal: value, source: value };
        }
        if (!atom) {
          failed = true;
          return;
        }
        if (source[index] === '*') {
          index++;
          const lazy = source[index] === '?';
          if (lazy) index++;
          atom = { kind: 'star', child: atom, lazy, source: source.slice(start, index) };
        }
        items.push(atom);
      }
      branches.push({ kind: 'sequence', items });
      if (branches.length > MAX_WORDS) {
        failed = true;
        return;
      }
      if (source[index] !== '|') break;
      index++;
    }
    return { kind: 'alternatives', branches };
  };

  const tree = expression(0);
  return !failed && index === source.length ? tree : undefined;
};

const literalWords = node => {
  if (node.kind === 'atom') return node.literal === null ? undefined : [node.literal];
  if (node.kind === 'star') return;
  if (node.kind === 'group') return literalWords(node.child);
  if (node.kind === 'alternatives') {
    const words = [];
    let length = 0;
    for (const branch of node.branches) {
      const values = literalWords(branch);
      if (!values) return;
      for (const value of values) {
        words.push(value);
        length += value.length;
        if (words.length > MAX_WORDS || length > MAX_EXPANDED_LENGTH) return;
      }
    }
    return words;
  }
  let words = [''];
  for (const item of node.items) {
    const values = literalWords(item);
    if (!values || words.length * values.length > MAX_WORDS) return;
    const combined = [];
    let length = 0;
    for (const prefix of words) {
      for (const value of values) {
        const word = prefix + value;
        length += word.length;
        if (length > MAX_EXPANDED_LENGTH) return;
        combined.push(word);
      }
    }
    words = combined;
  }
  return words;
};

const deterministicWords = (words, flags) => {
  if (words.length === 0 || words.some(word => word.length === 0)) return false;
  if (words.length === 1) return true;
  if (flags.includes('i')) {
    // JavaScript's Unicode case equivalences are not String#toLowerCase. Restrict
    // alternative comparisons to the ASCII domain where this fold is exact.
    if (words.some(word => /[^\x00-\x7f]/.test(word))) return false;
    words = words.map(word => word.toLowerCase());
  }
  if (new Set(words).size !== words.length) return false;
  const first = new Set(words.map(word => word[0]));
  for (const short of words) {
    for (const long of words) {
      if (short.length >= long.length || !long.startsWith(short)) continue;
      // A shorter matching alternative cannot start another iteration when the
      // next symbol only continues the longer one. Thus js|jsx is unambiguous,
      // while a|aa and ab|abab are rejected. This is a local repetition proof,
      // not a claim about arbitrary surrounding regex supplied by a caller.
      if (first.has(long[short.length])) return false;
    }
  }
  return true;
};

const fixedSequence = node => {
  if (node.kind === 'atom') return true;
  if (node.kind === 'star') return false;
  if (node.kind === 'group') return fixedSequence(node.child);
  if (node.kind === 'alternatives') {
    return node.branches.length === 1 && fixedSequence(node.branches[0]);
  }
  return node.items.length > 0 && node.items.every(fixedSequence);
};

const unwrap = node => {
  while (node) {
    if (node.kind === 'group' && node.capture === false) {
      node = node.child;
    } else if (node.kind === 'alternatives' && node.branches.length === 1) {
      node = node.branches[0];
    } else if (node.kind === 'sequence' && node.items.length === 1) {
      node = node.items[0];
    } else {
      return node;
    }
  }
};

const literalCharacter = node => {
  node = unwrap(node);
  return node && node.kind === 'atom' && node.literal !== null && node.literal.length === 1
    ? node.literal
    : undefined;
};

const escapeCharacter = value => {
  if (/^[a-z\d]$/i.test(value)) return value;
  const code = value.charCodeAt(0);
  return code <= 0xff
    ? `\\x${code.toString(16).padStart(2, '0')}`
    : `\\u${code.toString(16).padStart(4, '0')}`;
};

const flatStarOutput = (tree, flags) => {
  const single = unwrap(tree);
  if (single && single.kind === 'star') {
    const atom = unwrap(single.child);
    if (atom && atom.kind === 'atom') {
      // (R*)+ and (R*)* both equal R* for a single character atom. The OUTER
      // extglob is greedy even when the inner star is lazy: use a greedy star
      // to preserve the split observed by any following/surrounding captures.
      // Never remove a capturing group to obtain this rewrite.
      return `${atom.source}*`;
    }
  }

  const body = unwrap(tree);
  const branches = body.kind === 'alternatives' ? body.branches : [body];
  const characters = [];
  let sawStar = false;
  for (const branch of branches) {
    const value = unwrap(branch);
    const literal = literalCharacter(value);
    if (literal !== undefined) {
      characters.push(literal);
      continue;
    }
    const items = value.kind === 'sequence' ? value.items : [value];
    if (items.length === 0) return;
    for (const item of items) {
      const repetition = unwrap(item);
      if (!repetition || repetition.kind !== 'star') return;
      const character = literalCharacter(repetition.child);
      if (character === undefined) return;
      sawStar = true;
      characters.push(character);
    }
  }
  if (!sawStar) return;
  // Under Unicode flags adjacent high/low surrogate escapes inside a class can
  // become one code point, although the original alternatives match lone code
  // units separately. Do not certify a class union with that language change.
  if ((flags.includes('u') || flags.includes('v')) && characters.some(value => /[\ud800-\udfff]/.test(value))) return;
  // Each starred sequence can contribute any one of its characters with every
  // other star empty. Repeating these branches therefore gives the union's star.
  // This preserves ALL branches, including the older 2.x dropped-branch case.
  const escaped = [...new Set(characters)].map(escapeCharacter);
  return escaped.length === 1 ? `${escaped[0]}*` : `[${escaped.join('')}]*`;
};

const analyzeCompiledRepeat = (source, options = {}) => {
  if (typeof source !== 'string' || source.length === 0 || source.length > MAX_SOURCE_LENGTH) return risky();
  options = options || {};
  const flags = options.flags || (options.nocase ? 'i' : '');
  if (typeof flags !== 'string') return risky();
  try {
    new RegExp('', flags);
  } catch {
    return risky();
  }
  const tree = parseCompiledBody(source, flags);
  if (!tree) return risky();
  const words = literalWords(tree);
  if (words ? deterministicWords(words, flags) : fixedSequence(tree)) {
    return { risky: false };
  }
  // A boolean match equivalence does not establish capture equivalence. Preserve
  // captured finite bodies above; decline nullable-star rewrites in capture mode.
  if (!options.capture) {
    const safeOutput = flatStarOutput(tree, flags);
    if (safeOutput) return { risky: true, safeOutput };
  }
  return risky();
};

module.exports = { analyzeCompiledRepeat };
