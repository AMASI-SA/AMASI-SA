import ProductControlCenterPanel from "./ProductControlCenterPanel";

/**
 * Compatibility wrapper.
 *
 * The legacy control center used a raw textarea for product HTML. All callers
 * now use the governed ProductControlCenterPanel so the visual editor is the
 * default and HTML source remains an explicit advanced mode only.
 */
export default function ProductControlCenter({ productId, product, onPublished }) {
  return <ProductControlCenterPanel productId={productId} product={product} onPublished={onPublished} />;
}
