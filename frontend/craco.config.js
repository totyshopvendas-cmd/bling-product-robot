// craco.config.js
const path = require("path");
require("dotenv").config();

// Check if we're in development/preview mode (not production build)
// Craco sets NODE_ENV=development for start, NODE_ENV=production for build
const isDevServer = process.env.NODE_ENV !== "production";

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
      webpackConfig.plugins = (webpackConfig.plugins || []).filter((plugin) => {
        const name = plugin?.constructor?.name || "";
        return !["ForkTsCheckerWebpackPlugin", "ESLintWebpackPlugin"].includes(name);
      });

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
  devServerConfig.host = "0.0.0.0";
  devServerConfig.allowedHosts = "all";
  devServerConfig.historyApiFallback = true;
  devServerConfig.headers = {
    ...(devServerConfig.headers || {}),
    "Access-Control-Allow-Origin": "*",
  };
  if (devServerConfig.client) {
    devServerConfig.client.webSocketURL = "auto://0.0.0.0:0/ws";
  } else {
    devServerConfig.client = { webSocketURL: "auto://0.0.0.0:0/ws" };
  }
  const backend = process.env.BACKEND_PROXY_TARGET || "http://127.0.0.1:8000";
  devServerConfig.proxy = {
    ...(typeof devServerConfig.proxy === "object" ? devServerConfig.proxy : {}),
    "/api": {
      target: backend,
      changeOrigin: true,
    },
  };

  if (config.enableHealthCheck && setupHealthEndpoints && healthPluginInstance) {
    const originalSetupMiddlewares = devServerConfig.setupMiddlewares;

    devServerConfig.setupMiddlewares = (middlewares, devServer) => {
      if (originalSetupMiddlewares) {
        middlewares = originalSetupMiddlewares(middlewares, devServer);
      }
      setupHealthEndpoints(devServer, healthPluginInstance);
      return middlewares;
    };
  }

  return devServerConfig;
};

// Optional Emergent overlay — off unless ENABLE_VISUAL_EDITS=true
if (isDevServer && process.env.ENABLE_VISUAL_EDITS === "true") {
  try {
    const { withVisualEdits } = require("@emergentbase/visual-edits/craco");
    webpackConfig = withVisualEdits(webpackConfig);
  } catch (err) {
    if (err.code === "MODULE_NOT_FOUND") {
      console.warn("[visual-edits] pacote não instalado — ignorado.");
    } else {
      throw err;
    }
  }
}

module.exports = webpackConfig;
