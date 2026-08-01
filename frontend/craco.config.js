// craco.config.js
const path = require("path");
require("dotenv").config();

// Check if we're in development/preview mode (not production build)
// Craco sets NODE_ENV=development for start, NODE_ENV=production for build
const isDevServer = process.env.NODE_ENV !== "production";

const LEGAL_NOINDEX_PATHS = new Set([
  "/privacy-policy",
  "/data-deletion",
  "/terms",
]);
const LEGAL_NOINDEX_DIRECTIVES =
  "noindex, nofollow, noarchive, nosnippet, noimageindex";

function normalizeRequestPath(rawUrl) {
  let pathname = "/";
  try {
    pathname = new URL(String(rawUrl || "/"), "http://localhost").pathname;
  } catch (_) {
    pathname = String(rawUrl || "/").split(/[?#]/, 1)[0] || "/";
  }
  if (pathname.length > 1 && pathname.endsWith("/")) {
    return pathname.slice(0, -1);
  }
  return pathname;
}

function legalNoIndexHeaders(req, res, next) {
  if (LEGAL_NOINDEX_PATHS.has(normalizeRequestPath(req.url))) {
    res.setHeader("X-Robots-Tag", LEGAL_NOINDEX_DIRECTIVES);
  }
  next();
}

// Environment variable overrides
const config = {
  enableHealthCheck: process.env.ENABLE_HEALTH_CHECK === "true",
};

// Conditionally load health check modules only if enabled
let WebpackHealthPlugin;
let setupHealthEndpoints;
let healthPluginInstance;

if (config.enableHealthCheck) {
  WebpackHealthPlugin = require("./plugins/health-check/webpack-health-plugin");
  setupHealthEndpoints = require("./plugins/health-check/health-endpoints");
  healthPluginInstance = new WebpackHealthPlugin();
}

let webpackConfig = {
  eslint: {
    configure: {
      extends: ["plugin:react-hooks/recommended"],
      rules: {
        "react-hooks/rules-of-hooks": "error",
        "react-hooks/exhaustive-deps": "warn",
      },
    },
  },
  webpack: {
    alias: {
      '@': path.resolve(__dirname, 'src'),
    },
    configure: (webpackConfig) => {

      // Add ignored patterns to reduce watched directories
        webpackConfig.watchOptions = {
          ...webpackConfig.watchOptions,
          ignored: [
            '**/node_modules/**',
            '**/.git/**',
            '**/build/**',
            '**/dist/**',
            '**/coverage/**',
            '**/public/**',
        ],
      };

      // Add health check plugin to webpack if enabled
      if (config.enableHealthCheck && healthPluginInstance) {
        webpackConfig.plugins.push(healthPluginInstance);
      }
      return webpackConfig;
    },
  },
};

webpackConfig.devServer = (devServerConfig) => {
  const originalSetupMiddlewares = devServerConfig.setupMiddlewares;

  devServerConfig.setupMiddlewares = (middlewares, devServer) => {
    if (originalSetupMiddlewares) {
      middlewares = originalSetupMiddlewares(middlewares, devServer);
    }

    if (!middlewares.some((item) => item?.name === "mezan-legal-noindex-headers")) {
      middlewares.unshift({
        name: "mezan-legal-noindex-headers",
        middleware: legalNoIndexHeaders,
      });
    }

    if (config.enableHealthCheck && setupHealthEndpoints && healthPluginInstance) {
      setupHealthEndpoints(devServer, healthPluginInstance);
    }

    return middlewares;
  };

  return devServerConfig;
};

// Wrap with visual edits (automatically adds babel plugin, dev server, and overlay in dev mode)
if (isDevServer) {
  try {
    const { withVisualEdits } = require("@emergentbase/visual-edits/craco");
    webpackConfig = withVisualEdits(webpackConfig);
  } catch (err) {
    if (err.code === 'MODULE_NOT_FOUND' && err.message.includes('@emergentbase/visual-edits/craco')) {
      console.warn(
        "[visual-edits] @emergentbase/visual-edits not installed — visual editing disabled."
      );
    } else {
      throw err;
    }
  }
}

module.exports = webpackConfig;
