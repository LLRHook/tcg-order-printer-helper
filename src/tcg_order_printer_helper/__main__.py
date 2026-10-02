import argparse, json, sys
from pathlib import Path
from . import core, native_host, wizard

def main(argv=None):
    parser=argparse.ArgumentParser(prog='tcg-order-printer-helper',description='Local print helper for the TCG Order Printer extension.')
    parser.add_argument('--state',type=Path,default=core.DEFAULT_STATE,help=argparse.SUPPRESS)
    sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('setup',help='Run the setup wizard')
    p.add_argument('--printer'); p.add_argument('--return-address',action='append',metavar='LINE')
    p.add_argument('--extension-id',action='append',default=[]); p.add_argument('--non-interactive',action='store_true')
    p.add_argument('--test-print',action='store_true')
    sub.add_parser('doctor',help='Check the installation')
    sub.add_parser('printers',help='List printers')
    p=sub.add_parser('uninstall',help='Unregister from browsers'); p.add_argument('--purge',action='store_true',help='Also delete settings, history and stored slips')
    p=sub.add_parser('host',help=argparse.SUPPRESS); p.add_argument('origin',nargs='?',default=''); p.add_argument('rest',nargs='*')
    args=parser.parse_args(argv)
    if args.command=='host':
        return native_host.serve(args.origin,state=args.state)
    if args.command=='setup':
        wizard.run(args.state,args.printer,args.return_address,args.extension_id,not args.non_interactive,args.test_print)
    elif args.command=='doctor':
        return 0 if wizard.doctor(args.state) else 1
    elif args.command=='printers':
        print(json.dumps(core.list_printers(),indent=2))
    elif args.command=='uninstall':
        print('Unregistered from: '+(', '.join(wizard.unregister()) or 'none'))
        if args.purge:
            import shutil; shutil.rmtree(args.state,ignore_errors=True); print(f'Deleted {args.state}')
    return 0

if __name__=='__main__':
    sys.exit(main())
