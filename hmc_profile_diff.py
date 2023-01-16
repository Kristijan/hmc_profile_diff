#!/usr/bin/env python3
"""This script compares the HMC profiles of two LPAR's"""

import argparse
import getpass
import sys
from pathlib import Path
import yaml
from rich.console import Console
from rich.table import Table
from common import hmc_lpar_attribs as hmcconnect

# rich colours
console = Console()

# Set the base path to be used for reading files from the local filesystem
base_path = Path(__file__).parent

# Setup argument parser
parser = argparse.ArgumentParser(
    formatter_class=argparse.RawDescriptionHelpFormatter,
    description='''Compares the HMC profiles of two LPAR's and highlights any differences.
    ''',
    epilog='''Format for the input file when using --file:
    prod01:dr01
    prod02:dr02
    
Output colours:
    green : Values match
      red : Values don't match or value missing''')
group = parser.add_mutually_exclusive_group(required=True)
group.add_argument('--lpars', nargs='*', help='''pair(s) of LPAR's to compare separated by a space. LPAR names are case sensitive. (e.g. --lpars prod01:dr01 prod02:dr02)''')
group.add_argument('--file', help='''file location of LPAR's to compare''')
parser.add_argument('--hmcs', help='''override HMC's listed in configuration file''')
parser.add_argument('--diffonly', action='store_true', default=False, help='''only show the differences between LPAR's''')

# Print help and exit if no args passed
if len(sys.argv[1:]) == 0:
    parser.print_help()
    parser.exit()

# Parse command line args
results = parser.parse_args()
if results.lpars:
    lpars = results.lpars
elif results.file:
    lparfile = (base_path / results.file).resolve()
    try:
        with open(lparfile, encoding='utf-8', mode='r') as f:
            lpars = [line.strip() for line in f]
    except FileNotFoundError:
        console.print(f'\n[red]Error: File {lparfile} not found.[/red]\n')
        sys.exit()

# Read configuration file settings
cfgfile = (base_path / 'config/config.yaml').resolve()
try:
    with open(cfgfile, encoding='utf-8', mode='r') as cfgfile:
        cfg = yaml.load(cfgfile, Loader=yaml.FullLoader)
        hmcs = cfg['hmcs']
        debug = cfg['debug']
except (NameError, FileNotFoundError):
    console.print('\n[red]Error: Configuration file not found.[/red]\n')
    sys.exit()

# If HMC's were specified on the command line, use them instead of the configuration file
if results.hmcs:
    hmcs = results.hmcs.split(':')

# Check that we have a list of HMC's
if not hmcs:
    console.print('\n[red]Error: No HMCs have been configured.[/red]\n')
    sys.exit()

# Turn off exceptions unless debug is set
if not debug:
    sys.tracebacklimit=0

# Get HMC credentials (assumes user/pass is the same on all HMC's)
try:
    hmc_user = input('HMC username: ')
    hmc_pass = getpass.getpass(prompt='HMC password: ', stream=sys.stderr)
except (KeyboardInterrupt) as error:
    if debug:
        raise

    console.print('\n\nKeyboard interrupt detected')
    sys.exit()

# Login to HMC and get LPAR profile
for lpar_pair in lpars:
    # LPAR names passed as args to script
    lpar1_name = lpar_pair.split(':')[0]
    lpar2_name = lpar_pair.split(':')[1]

    # Connect to HMC and get LPAR attributes
    lpar1_data, lpar2_data = None, None
    for hostname in hmcs:
        if isinstance(lpar1_data, dict) and isinstance(lpar2_data, dict):
            break

        if debug:
            hmc = hmcconnect.HMC(hostname, hmc_user, hmc_pass)
            if not isinstance(lpar1_data, dict):
                lpar1_data = hmc.get_lpar_config(lpar1_name)
            if not isinstance(lpar2_data, dict):
                lpar2_data = hmc.get_lpar_config(lpar2_name)
            hmc.logoff()
        else:
            with console.status("[green]Scanning HMC's for LPAR profiles...[/green]") as scanning:
                hmc = hmcconnect.HMC(hostname, hmc_user, hmc_pass)
                if not isinstance(lpar1_data, dict):
                    lpar1_data = hmc.get_lpar_config(lpar1_name)
                if not isinstance(lpar2_data, dict):
                    lpar2_data = hmc.get_lpar_config(lpar2_name)
                hmc.logoff()

    # Check that we have data for all LPAR's
    # - Return codes from get_lpar_config function
    #    1 - LPAR not found
    #    2 - Error encountered searching for LPAR
    #    3 - LPAR found, no associated default profile
    if lpar1_data == 1:
        console.print(f'[red]LPAR not found: {lpar1_name}[/red]')
        sys.exit()
    elif lpar1_data == 2:
        console.print(f'[red]Error encountered obtaining data: {lpar1_name}[/red]')
        sys.exit()
    elif lpar1_data == 3:
        console.print(f'[red]No default profile found: {lpar1_name}[/red]')
        sys.exit()
    elif lpar2_data == 1:
        console.print(f'[red]LPAR not found: {lpar2_name}[/red]')
        sys.exit()
    elif lpar2_data == 2:
        console.print(f'[red]Error encountered obtaining data: {lpar2_name}[/red]')
        sys.exit()
    elif lpar2_data == 3:
        console.print(f'[red]No default profile found: {lpar2_name}[/red]')
        sys.exit()

    # Check if we have identical keys in both datasets, and pad with 'missing' if not
    lpar1_keys = set(lpar1_data.keys())
    lpar2_keys = set(lpar2_data.keys())
    allkeys = lpar1_keys | lpar2_keys
    for k in allkeys:
        if k not in lpar1_data.keys():
            lpar1_data[k] = 'missing'
        if k not in lpar2_data.keys():
            lpar2_data[k] = 'missing'

    # Create a list of keys with the same value
    samevalues = [k for k in lpar1_data if lpar1_data[k] == lpar2_data[k]]

    # Generate table
    difftable = Table(header_style="bold blue")
    difftable.add_column("Attribute")
    difftable.add_column(lpar1_name)
    difftable.add_column(lpar2_name)
    primarykeys = ['General', 'Processor', 'Memory', 'Network', 'vFC', 'vSCSI']
    for primarykey in primarykeys:
        for k in sorted(allkeys):
            if k.startswith(primarykey):
                if primarykey in ('Network', 'vFC', 'vSCSI'):
                    k_printname = k.split("_", 1)[1]
                else:
                    k_printname = k.rsplit("_", 1)[1]

                if results.diffonly:
                    if k not in samevalues:
                        difftable.add_row(k_printname, f'[red]{lpar1_data[k]}[/red]', f'[red]{lpar2_data[k]}[/red]')
                else:
                    if k in samevalues:
                        difftable.add_row(k_printname, f'[green]{lpar1_data[k]}[/green]', f'[green]{lpar2_data[k]}[/green]')
                    else:
                        difftable.add_row(k_printname, f'[red]{lpar1_data[k]}[/red]', f'[red]{lpar2_data[k]}[/red]')

    # Print table
    console.print(difftable)
