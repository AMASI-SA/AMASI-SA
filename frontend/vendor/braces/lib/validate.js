'use strict';

// The public APIs also accept caller-supplied ASTs. Validate iteratively before
// recursive walkers or implicit value coercion can consume an untrusted AST.
module.exports = (ast, maxDepth) => {
  const pending = [{ node: ast, depth: ast && ast.type === 'root' ? 0 : 1 }];
  while (pending.length) {
    const { node, depth } = pending.pop();
    if (!node || typeof node !== 'object' || Array.isArray(node)) {
      throw new TypeError('AST node must be an object');
    }
    if (node.value !== undefined && typeof node.value !== 'string') {
      throw new TypeError('AST value must be a string');
    }
    if (node.nodes !== undefined) {
      if (!Array.isArray(node.nodes)) throw new TypeError('AST nodes must be an array');
      if (depth > maxDepth) {
        throw new RangeError(`AST depth (${depth}), exceeds max depth (${maxDepth})`);
      }
      for (const child of node.nodes) {
        pending.push({ node: child, depth: child && child.nodes ? depth + 1 : depth });
      }
    }
  }
};
