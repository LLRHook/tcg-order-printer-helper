"""First-run setup: printer, return address, allowed extensions, browser registration."""
import json, os, re, sys, tempfile
from pathlib import Path
from . import core
from .native_host import HOST_NAME, EXTENSION_ORIGIN

# ID of the unpacked development build (pinned by the manifest `key`). The Web
# Store build gets its own ID, which the extension's setup page displays.
DEV_EXTENSION_ID='pidblibebfmenlpcbnajfcdoikdldhgj'
EXTENSION_ID=re.compile(r'^[a-p]{32}$')
SUPPORT=Path.home()/'Library/Application Support'
# Chromium-family browsers on macOS and where each reads native host manifests.
BROWSERS={
    'Google Chrome':'Google/Chrome',
    'Google Chrome Beta':'Google/Chrome Beta',
    'Google Chrome Canary':'Google/Chrome Canary',
    'Chromium':'Chromium',
    'Microsoft Edge':'Microsoft Edge',
    'Brave':'BraveSoftware/Brave-Browser',
    'Vivaldi':'Vivaldi',
    'Arc':'Arc/User Data',
    'Helium':'net.imput.helium',
}

def host_manifest_paths(installed_only=True):
    return {name:SUPPORT/rel/'NativeMessagingHosts'/f'{HOST_NAME}.json'
            for name,rel in BROWSERS.items() if not installed_only or (SUPPORT/rel).is_dir()}

def write_launcher(state):
    # Browsers launch the host without a shell environment; pin the venv's
    # interpreter so the host never depends on whatever python3 is on PATH.
    launcher=state/'bin'/'native-host'
    launcher.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" -m tcg_order_printer_helper host "$@"\n')
    os.chmod(launcher,0o700)
    return launcher

def register(state,extension_ids):
    launcher=write_launcher(state)
    manifest={'name':HOST_NAME,'description':'TCG Order Printer helper','path':str(launcher),'type':'stdio',
              'allowed_origins':[f'chrome-extension://{i}/' for i in extension_ids]}
    written=[]
    for browser,path in host_manifest_paths().items():
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(manifest,indent=2)+'\n')
        written.append(browser)
    return written

def unregister():
    removed=[]
    for browser,path in host_manifest_paths(installed_only=False).items():
        if path.exists(): path.unlink(); removed.append(browser)
    return removed

def test_print(conf):
    sample=['Test Recipient','123 Example St','Springfield, IL 62701']
    with tempfile.TemporaryDirectory() as tmp:
        from pypdf import PdfWriter
        writer=PdfWriter(); writer.add_page(core.label(sample,conf['return_address'],'TEST0000-000000-00000'))
        path=Path(tmp)/'test-label.pdf'
        with open(path,'wb') as stream: writer.write(stream)
        return core.submit(path,conf,'TCG Order Printer test label')

def ask(prompt,default=None):
    suffix=f' [{default}]' if default else ''
    answer=input(f'{prompt}{suffix}: ').strip()
    return answer or default or ''

def choose_printer(printers):
    if not printers: raise SystemExit('No printers found. Add your label printer in System Settings > Printers & Scanners, then run setup again.')
    print('\nPrinters:')
    suggested=next((i for i,p in enumerate(printers) if p['label_4x6']),0)
    for i,p in enumerate(printers,1):
        tags=[t for t,on in (('4x6 labels',p['label_4x6']),('system default',p['default'])) if on]
        print(f'  {i}. {p["name"]}'+(f'  ({", ".join(tags)})' if tags else ''))
    while True:
        answer=ask('Label printer number',str(suggested+1))
        if answer.isdigit() and 1<=int(answer)<=len(printers): return printers[int(answer)-1]
        print('  Enter one of the numbers above.')

def ask_return_address(existing):
    print('\nReturn address printed on every label (2-4 lines, e.g. shop name, street, city/state/ZIP).')
    if existing:
        print('  Current: '+' / '.join(existing))
        if ask('Keep it? (y/n)','y').lower().startswith('y'): return existing
    lines=[]
    while len(lines)<4:
        line=ask(f'  Line {len(lines)+1}'+(' (blank to finish)' if len(lines)>=2 else ''))
        if not line:
            if len(lines)>=2: break
            print('  At least 2 lines are required.'); continue
        lines.append(line)
    return lines

def ask_extension_ids(existing):
    ids=list(dict.fromkeys(existing+[DEV_EXTENSION_ID]))
    print('\nThe extension\'s setup page shows its ID. Paste it here if it is not listed below.')
    print('  Allowed: '+', '.join(ids))
    while True:
        answer=ask('  Extension ID (blank to keep)')
        if not answer: return ids
        if EXTENSION_ID.fullmatch(answer): return list(dict.fromkeys(ids+[answer]))
        print('  An extension ID is 32 letters a-p.')

def run(state,printer=None,return_address=None,extension_ids=None,interactive=True,do_test_print=False):
    try: existing=json.loads((state/'config.json').read_text())
    except (FileNotFoundError,ValueError): existing={}
    printers=core.list_printers()
    if interactive:
        print('TCG Order Printer setup')
        printer=printer or choose_printer(printers)['name']
        return_address=return_address or ask_return_address(existing.get('return_address'))
        extension_ids=ask_extension_ids(list(extension_ids or existing.get('extension_ids',[])))
    else:
        printer=printer or existing.get('printer')
        return_address=return_address or existing.get('return_address')
        extension_ids=list(dict.fromkeys(list(extension_ids or existing.get('extension_ids',[]))+[DEV_EXTENSION_ID]))
    if printer not in {p['name'] for p in printers}: raise SystemExit(f'Printer not found: {printer}')
    if not all(EXTENSION_ID.fullmatch(i) for i in extension_ids): raise SystemExit('Invalid extension ID.')
    if not next(p for p in printers if p['name']==printer)['label_4x6']:
        print(f'Warning: {printer} does not list 4x6 (w288h432) media; labels may be scaled or cropped.')
    conf={**existing,'printer':printer,'return_address':return_address,'extension_ids':extension_ids,
          'lp_options':existing.get('lp_options',core.DEFAULT_LP_OPTIONS),
          'retention_days':existing.get('retention_days',core.DEFAULT_RETENTION_DAYS)}
    try: core.save_config(state,conf)
    except core.SetupRequired as error: raise SystemExit(f'Setup incomplete: {error}')
    browsers=register(state,extension_ids)
    if not browsers: raise SystemExit('No supported browser found (Chrome, Edge, Brave, Arc, Vivaldi, Chromium, Helium).')
    print(f'\nSaved. Printer: {printer}. Registered with: {", ".join(browsers)}.')
    if interactive and not do_test_print:
        do_test_print=ask('Print a test label now? (y/n)','n').lower().startswith('y')
    if do_test_print:
        print(f'Test label sent ({test_print(conf)}).')
    print('\nDone. Restart your browser, then click "Check connection" on the extension\'s setup page.')
    return conf

def doctor(state):
    checks=[]
    try: conf=core.load_config(state); checks.append(('config',True,str(state/'config.json')))
    except core.SetupRequired as error: conf=None; checks.append(('config',False,str(error)))
    if conf:
        checks.append(('printer',core.printer_ready(conf['printer']),conf['printer']))
        launcher=state/'bin'/'native-host'
        checks.append(('launcher',os.access(launcher,os.X_OK),str(launcher)))
        for browser,path in host_manifest_paths().items():
            ok=False
            try:
                manifest=json.loads(path.read_text())
                ok=manifest['path']==str(launcher) and all(f'chrome-extension://{i}/' in manifest['allowed_origins'] for i in conf['extension_ids'])
            except (FileNotFoundError,ValueError,KeyError): pass
            checks.append((f'host:{browser}',ok,str(path)))
    for name,ok,detail in checks: print(f'{"OK " if ok else "BAD"} {name:24} {detail}')
    return all(ok for _,ok,_ in checks)
