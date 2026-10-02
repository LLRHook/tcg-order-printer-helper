# TCG Order Printer — helper

Local print helper for the [TCG Order Printer](https://github.com/LLRHook/tcg-order-printer) browser extension. With it, one click on a TCGplayer order page prints the official packing slip **and** a matching 4×6 address label straight to your label printer, with no print dialog.

**Requirements:** macOS, Python 3.10+ ([python.org](https://www.python.org/downloads/macos/) recommended), a 4×6 label printer added in System Settings → Printers & Scanners, and a Chromium browser (Chrome, Edge, Brave, Arc, Vivaldi, Chromium or Helium).

## Install

```sh
git clone https://github.com/LLRHook/tcg-order-printer-helper.git
cd tcg-order-printer-helper
./install.sh
```

The setup wizard asks for:

1. **Label printer:** printers that support 4×6 media are marked.
2. **Return address:** 2–4 lines printed on every label.
3. **Extension ID:** copy it from the extension's setup page (it opens when you install the extension).

It then registers the helper with every supported browser it finds and can print a test label. Restart the browser and click **Check connection** on the extension's setup page.

Re-run `./install.sh` any time to upgrade or change settings.

## Commands

```sh
H="$HOME/Library/Application Support/TCGOrderPrinter/runtime/bin/python -m tcg_order_printer_helper"
$H setup              # re-run the wizard
$H doctor             # check config, printer and browser registration
$H printers           # list printers
$H uninstall [--purge]  # unregister; --purge also deletes settings and history
```

## How it works

The extension captures the packing-slip PDF that TCGplayer's own **Print Default** export produces and passes the bytes to this helper over Chrome native messaging. The helper:

- checks the PDF is complete and contains exactly the order you clicked,
- reads the ship-to address from the slip and lays out a 4×6 label with your return address,
- sends slip + label to your printer as one CUPS job, and
- records each click by request ID so a retry never prints twice.

## Privacy

Everything stays on your computer. The helper makes no network requests. Slips and labels are stored under `~/Library/Application Support/TCGOrderPrinter` with owner-only permissions and deleted after 7 days (`retention_days` in `config.json`).

## Development

```sh
python3 -m venv .venv && .venv/bin/pip install -e .
cd tests && ../.venv/bin/python -m unittest
# Optional regression over real exports (never commit them: they contain buyer addresses)
TCG_SAMPLES=~/Downloads ../.venv/bin/python -m unittest
```

## License

MIT
