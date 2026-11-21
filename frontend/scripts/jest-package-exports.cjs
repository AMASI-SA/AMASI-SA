// CRA's Jest 27 predates the conditional subpath exports used by the locked
// Radix version. Keep its normal resolution, then let Node resolve that package.
module.exports = (request, options) => {
  try { return options.defaultResolver(request, options); }
  catch (error) {
    if (!request.startsWith('@radix-ui/') && !request.startsWith('react-router')) throw error;
    return require.resolve(request, {paths: [options.basedir]});
  }
};
