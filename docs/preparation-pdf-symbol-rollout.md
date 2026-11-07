# Preparation PDF symbols for newly created orders

At an approved rollout, set `PREPARATION_PDF_SYMBOLS_FROM` to the rollout's
fixed ISO 8601 timestamp with an offset, identical on all backend replicas.
Do not advance this timestamp on a restart or subsequent release.

The PDF uses the order creation timestamp already saved with each preparation
line. Orders created at or after the cutoff use the new symbol rendering.
Orders created earlier, or without an unambiguous timestamp, retain their
previous rendering even when reprinted alongside new orders. An unset or
invalid cutoff leaves the feature inactive. There are no database changes.

The embedded print font is a BMP subset of
[Noto Sans Symbols 2](https://github.com/notofonts/symbols), licensed under
the accompanying `backend/fonts/NotoSansSymbols2-OFL.txt`. Its upstream
[TTF](https://github.com/google/fonts/blob/7b6724ac7ececc713e9ba93af309f7520c9a80a3/ofl/notosanssymbols2/NotoSansSymbols2-Regular.ttf)
has SHA-256 `7d5fb73b7ca67a6798101741f5d280a3d016a56a197afcd4199dbb57b4b82a21`.
The subset was generated with FontTools 4.68.0:

```sh
python -m fontTools.subset NotoSansSymbols2-Regular.ttf --unicodes=U+0000-FFFF --output-file=NotoSansSymbols2-Print.ttf
```
