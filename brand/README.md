# Box Out Fantasy brand assets

Vector logo files. Every letter is drawn as a path, so the SVGs have no font
dependencies and render identically everywhere.

| File | Use |
| --- | --- |
| `box-out-logo.svg` | Full lockup on light background (mark + wordmark + tagline) |
| `box-out-logo-dark.svg` | Full lockup on dark background |
| `box-out-icon.svg` | Mark only, rounded square. App icon, favicon, avatar |
| `*.png` | 1200x1200 rendered previews of the files above |

## Colors

| Name | Hex |
| --- | --- |
| Ink | `#2B2D31` |
| Orange | `#F26A1B` |
| Paper | `#F7F6F3` |
| Dark background | `#1B1C1F` |

## Regenerating previews

Headless Chromium renders the SVGs to PNG:

```sh
chrome --headless --no-sandbox --hide-scrollbars --window-size=1200,1200 \
  --screenshot=box-out-logo.png file://$PWD/box-out-logo.svg
```
