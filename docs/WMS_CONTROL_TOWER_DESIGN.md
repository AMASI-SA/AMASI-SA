# Mezan OS — Warehouse Control Tower

## UX contract

The warehouse workspace is a single hierarchical planner, not a collection of repeated forms.

Hierarchy:

```
Branch / facility
├── Main area (synthetic for simple branches)
│   └── Storage units (cabinets / racks)
└── Dynamic sections
    ├── Capabilities (storage, assembly, engraving, packing, shipping, QC, etc.)
    └── Storage units and future work resources
```

## Interface

- One branch selector and one contextual **Add** menu.
- Right-side hierarchy tree for navigation.
- Center operational layout/canvas.
- Left-side inspector for the selected branch, section, or storage unit.
- Creation happens in a single modal, never in duplicated always-visible forms.
- A simple branch can use the synthetic **Main area** without manually creating sections.
- Advanced branches add any number of dynamic sections.
- Storage labels and barcodes are printed from the selected storage unit.

## Compatibility

Existing direct branch cabinets remain visible under the synthetic Main area. Section cabinets remain under their section. New UI hides backend route differences and exposes one storage-unit workflow.