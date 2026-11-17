'use strict';

const path = require('path');
const win32 = process.platform === 'win32';
const {
  REGEX_BACKSLASH,
  REGEX_SPECIAL_CHARS,
  REGEX_SPECIAL_CHARS_GLOBAL
} = require('./constants');

exports.isObject = val => val !== null && typeof val === 'object' && !Array.isArray(val);
exports.hasRegexChars = str => REGEX_SPECIAL_CHARS.test(str);
exports.isRegexChar = str => str.length === 1 && exports.hasRegexChars(str);
exports.escapeRegex = str => str.replace(REGEX_SPECIAL_CHARS_GLOBAL, '\\$1');
exports.toPosixSlashes = str => str.replace(REGEX_BACKSLASH, '/');

const isLineTerminator = code => code === 10 || code === 13 || code === 0x2028 || code === 0x2029;

exports.removeBackslashes = str => {
  const length = str.length;
  const bracketEnds = new Int32Array(length);
  let nextClose = -1;

  // Find each opening bracket's first eligible close in one reverse pass.
  // The old dot cannot cross a line terminator, but its final non-backslash
  // body character may be a line terminator immediately before the close.
  for (let index = length - 1; index > 0; index--) {
    const code = str.charCodeAt(index);
    if (code !== 92 && str.charCodeAt(index + 1) === 93) {
      nextClose = index + 1;
    } else if (isLineTerminator(code)) {
      nextClose = -1;
    }
    if (str.charCodeAt(index - 1) === 91) bracketEnds[index - 1] = nextClose;
  }

  const chunks = [];
  let start = 0;
  for (let index = 0; index < length; index++) {
    // A preserved bracket span has at least one body character. All indices
    // refer to the original input, so deleting a slash cannot create a span.
    if (bracketEnds[index] >= index + 2) {
      index = bracketEnds[index];
      continue;
    }
    if (str.charCodeAt(index) === 92 && index + 1 < length
      && !isLineTerminator(str.charCodeAt(index + 1))) {
      if (start < index) chunks.push(str.slice(start, index));
      start = index + 1;
    }
  }
  chunks.push(str.slice(start));
  return chunks.join('');
};

exports.supportsLookbehinds = () => {
  const segs = process.version.slice(1).split('.').map(Number);
  if (segs.length === 3 && segs[0] >= 9 || (segs[0] === 8 && segs[1] >= 10)) {
    return true;
  }
  return false;
};

exports.isWindows = options => {
  if (options && typeof options.windows === 'boolean') {
    return options.windows;
  }
  return win32 === true || path.sep === '\\';
};

exports.escapeLast = (input, char, lastIdx) => {
  const idx = input.lastIndexOf(char, lastIdx);
  if (idx === -1) return input;
  if (input[idx - 1] === '\\') return exports.escapeLast(input, char, idx - 1);
  return `${input.slice(0, idx)}\\${input.slice(idx)}`;
};

exports.removePrefix = (input, state = {}) => {
  let output = input;
  if (output.startsWith('./')) {
    output = output.slice(2);
    state.prefix = './';
  }
  return output;
};

exports.wrapOutput = (input, state = {}, options = {}) => {
  const prepend = options.contains ? '' : '^';
  const append = options.contains ? '' : '$';

  let output = `${prepend}(?:${input})${append}`;
  if (state.negated === true) {
    output = `(?:^(?!${output}).*$)`;
  }
  return output;
};
