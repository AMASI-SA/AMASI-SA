/* Run after `vite build --manifest`. This measures transferred code, not latency. */
const fs = require("node:fs");
const path = require("node:path");
const zlib = require("node:zlib");

const build = path.resolve(__dirname, "../frontend/build");
const manifest = JSON.parse(fs.readFileSync(path.join(build, ".vite/manifest.json"), "utf8"));
const entry = Object.keys(manifest).find(key => manifest[key].isEntry);

function measure(keys) {
    const files = new Set();
    const visited = new Set();
    function visit(key) {
        if (visited.has(key)) return;
        visited.add(key);
        const chunk = manifest[key];
        if (!chunk) throw new Error(`Missing build manifest entry: ${key}`);
        if (chunk.file.endsWith(".js")) files.add(chunk.file);
        for (const imported of chunk.imports || []) visit(imported);
    }
    for (const key of keys) visit(key);
    let bytes = 0, gzipBytes = 0;
    for (const file of files) {
        const data = fs.readFileSync(path.join(build, file));
        bytes += data.length;
        gzipBytes += zlib.gzipSync(data).length;
    }
    return { files: [...files].sort(), bytes, gzip_bytes: gzipBytes };
}

const result = {
    method: "Transitive static JavaScript imports; gzip per file. Excludes CSS, API time, and device execution.",
    initial: measure([entry]),
    dashboard: measure([entry, "src/pages/AdvancedDashboard.jsx"]),
    fulfillment_shell: measure([entry, "src/pages/FulfillmentV2.jsx"]),
};
const output = process.argv[2];
if (output) fs.writeFileSync(output, JSON.stringify(result, null, 2) + "\n");
console.log(JSON.stringify(result, null, 2));
