from pathlib import Path

FRONTEND = Path('frontend/src/pages/OrderReview.jsx')
TEST = Path('frontend/src/pages/__tests__/FulfillmentV2.test.jsx')


def replace_once(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'{label}: expected one match, got {count}')
    return text.replace(old, new, 1)

frontend = FRONTEND.read_text(encoding='utf-8')
frontend = replace_once(
    frontend,
    '''function safeReceiptUrl(value) {
''',
    '''function imageIdentity(value) {
    const raw = String(value || "").trim();
    if (!raw) return "";
    try {
        const url = new URL(raw);
        return `${url.origin}${decodeURIComponent(url.pathname)}`.replace(/\\/+$/, "").toLowerCase();
    } catch {
        return raw.split(/[?#]/, 1)[0].replace(/\\/+$/, "").toLowerCase();
    }
}

function safeReceiptUrl(value) {
''',
    'image identity helper',
)
frontend = replace_once(
    frontend,
    '''    const specs = reviewProductSpecs(item);
    const gallery = Array.from(new Set((item.gallery || []).filter(Boolean))).filter((url) => url !== item.selected_image_url);
''',
    '''    const specs = reviewProductSpecs(item);
    const selectedIdentity = imageIdentity(item.selected_image_url);
    const gallery = [];
    const seenImageIdentities = new Set();
    for (const url of (item.gallery || []).filter(Boolean)) {
        const identity = imageIdentity(url);
        if (!identity || identity === selectedIdentity || seenImageIdentities.has(identity)) continue;
        seenImageIdentities.add(identity);
        gallery.push(url);
    }
''',
    'gallery dedup logic',
)
frontend = replace_once(
    frontend,
    '''                                const selected = url === item.selected_image_url;
''',
    '''                                const selected = imageIdentity(url) === selectedIdentity;
''',
    'selected identity comparison',
)
FRONTEND.write_text(frontend, encoding='utf-8')

test = TEST.read_text(encoding='utf-8')
test += '''\n\ntest("review gallery keeps selected image as main and removes duplicate URL variants", () => {\n  const source = require("fs").readFileSync(require("path").join(__dirname, "../OrderReview.jsx"), "utf8");\n  expect(source).toContain("function imageIdentity(value)");\n  expect(source).toContain("identity === selectedIdentity");\n  expect(source).toContain("seenImageIdentities.has(identity)");\n  expect(source).toContain("const selected = imageIdentity(url) === selectedIdentity");\n});\n'''
TEST.write_text(test, encoding='utf-8')

print('Review gallery selected-image dedup patch applied.')
