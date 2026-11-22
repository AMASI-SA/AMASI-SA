import React, { useState } from 'react';
import { BRANDS } from './brandIconModel';
import './brandIcon.css';

// Decorative: callers retain the readable resource name next to the mark.
export default function BrandIcon({ brand, fallbackName = 'building-2', fallbackTone = 'party', size = 'md', className = '' }) {
  const [failedSource, setFailedSource] = useState(null);
  const identity = Object.hasOwn(BRANDS, brand) ? BRANDS[brand] : null;
  const visible = identity && (!identity.src || identity.src !== failedSource);
  const dimensions = { sm: 28, md: 35, lg: 48 };
  const safeSize = Object.hasOwn(dimensions, size) ? size : 'md';
  return <span aria-hidden="true" className={`iconsquare mz-resource-icon mz-resource-icon--${safeSize} ${visible ? 'mz-resource-brand' : fallbackTone} ${className}`}
    data-brand={visible ? brand : undefined}>
    {visible ? identity.src ? <img src={identity.src} alt="" width={dimensions[safeSize]} height={dimensions[safeSize]}
      decoding="async" onError={() => setFailedSource(identity.src)} /> :
      <span className={`mz-brand-wordmark mz-brand-wordmark--${brand}`}>{identity.text}</span> :
      <svg className="ico" aria-hidden="true"><use href={`/operational-ui/icons.svg#${fallbackName}`} /></svg>}
  </span>;
}
