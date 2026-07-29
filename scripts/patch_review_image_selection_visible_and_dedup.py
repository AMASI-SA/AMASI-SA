from pathlib import Path

FRONTEND = Path('frontend/src/pages/OrderReview.jsx')
CONTRACT = Path('backend/tests/test_fulfillment_v2_contract.py')


def replace_once(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'{label}: expected one match, got {count}')
    return text.replace(old, new, 1)

frontend = FRONTEND.read_text(encoding='utf-8')
frontend = replace_once(
    frontend,
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
''',
    '''function imageIdentity(value) {
    const raw = String(value || "").trim();
    if (!raw) return "";
    const normalizePath = (pathname) => {
        const decoded = decodeURIComponent(pathname || "").toLowerCase();
        const filename = decoded.split("/").filter(Boolean).pop() || decoded;
        return filename
            .replace(/[-_](?:thumb|thumbnail|small|medium|large|original)(?=\\.|$)/g, "")
            .replace(/[-_]\\d{2,4}x\\d{2,4}(?=\\.|$)/g, "")
            .replace(/\\.(?:webp|avif)$/g, ".jpg");
    };
    try {
        const url = new URL(raw);
        return normalizePath(url.pathname);
    } catch {
        return normalizePath(raw.split(/[?#]/, 1)[0]);
    }
}
''',
    'strong image identity',
)
frontend = replace_once(
    frontend,
    '''    const [busy, setBusy] = useState(false);

    useEffect(() => {
        setPreparationNote(item.preparation_note || "");
        setInternalNote(item.internal_note || "");
    }, [item.internal_note, item.preparation_note]);
''',
    '''    const [busy, setBusy] = useState(false);
    const [visibleSelectedImage, setVisibleSelectedImage] = useState(item.selected_image_url || item.image_url || "");

    useEffect(() => {
        setPreparationNote(item.preparation_note || "");
        setInternalNote(item.internal_note || "");
    }, [item.internal_note, item.preparation_note]);

    useEffect(() => {
        if (item.selected_image_url) setVisibleSelectedImage(item.selected_image_url);
    }, [item.selected_image_url]);
''',
    'local selected image state',
)
frontend = replace_once(
    frontend,
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
    '''    const specs = reviewProductSpecs(item);
    const selectedIdentity = imageIdentity(visibleSelectedImage);
    const gallery = [];
    const seenImageIdentities = new Set();
    for (const url of [visibleSelectedImage, ...(item.gallery || [])].filter(Boolean)) {
        const identity = imageIdentity(url);
        if (!identity || seenImageIdentities.has(identity)) continue;
        seenImageIdentities.add(identity);
        gallery.push(url);
    }
''',
    'keep selected thumbnail and dedup',
)
frontend = replace_once(
    frontend,
    '''                        {item.selected_image_url ? (
                            <img src={item.selected_image_url} alt={item.name} className="h-full w-full object-cover" />
''',
    '''                        {visibleSelectedImage ? (
                            <img src={visibleSelectedImage} alt={item.name} className="h-full w-full object-cover" />
''',
    'main image local selection',
)
frontend = replace_once(
    frontend,
    '''                                        onClick={() => save({ selected_image_url: url }, "تم حفظ الصورة لهذه الخيارات وستُستخدم تلقائيًا لاحقًا.")}
''',
    '''                                        onClick={() => {
                                            setVisibleSelectedImage(url);
                                            save({ selected_image_url: url }, "تم حفظ الصورة لهذه الخيارات وستُستخدم تلقائيًا لاحقًا.");
                                        }}
''',
    'optimistic image selection',
)
FRONTEND.write_text(frontend, encoding='utf-8')

contract = CONTRACT.read_text(encoding='utf-8')
contract = contract.replace(
    "    assert 'identity === selectedIdentity' in frontend_source\n",
    "    assert 'for (const url of [visibleSelectedImage, ...(item.gallery || [])]' in frontend_source\n",
)
contract = contract.replace(
    "    assert 'seenImageIdentities.has(identity)' in frontend_source\n",
    "    assert 'seenImageIdentities.has(identity)' in frontend_source\n    assert 'setVisibleSelectedImage(url)' in frontend_source\n",
)
CONTRACT.write_text(contract, encoding='utf-8')

print('Review image selection visibility and dedup patch applied.')
