#!/usr/bin/env python3
"""
Insta360 X4 .insv File Repair Tool (modular version)
====================================================
Repairs broken/corrupted .insv video files from the Insta360 X4 camera.

Usage:
  python3 insv_repair.py <broken_file.insv> [--reference <good_file.insv>]
  python3 insv_repair.py <broken_file.insv> --scan
  python3 insv_repair.py <file.insv> --diagnose
"""

import argparse
import os
import sys
from diagnoser import INSVDiagnoser
from repairer import INSVRepairer

def main():
    parser = argparse.ArgumentParser(
        description='Insta360 X4 .insv File Repair Tool',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Diagnose a file (no repair):
  python3 insv_repair.py broken.insv --diagnose

  # Repair using a reference file (recommended):
  python3 insv_repair.py broken.insv --reference good_file.insv

  # Repair without a reference file (scan mode):
  python3 insv_repair.py broken.insv --scan

  # Specify output filename:
  python3 insv_repair.py broken.insv --scan -o repaired.insv
""")

    parser.add_argument('input', help='Path to the broken .insv file')
    parser.add_argument('--reference', '-r', help='Path to a known-good .insv reference file')
    parser.add_argument('--scan', '-s', action='store_true',
                        help='Scan mode: rebuild moov by scanning mdat (no reference needed)')
    parser.add_argument('--diagnose', '-d', action='store_true',
                        help='Diagnose only - do not repair')
    parser.add_argument('--output', '-o', help='Output file path')

    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"Error: File not found: {args.input}")
        sys.exit(1)

    if args.diagnose:
        diag = INSVDiagnoser(args.input)
        diag.diagnose()
        diag.close()
        return

    if args.reference:
        if not os.path.exists(args.reference):
            print(f"Error: Reference file not found: {args.reference}")
            sys.exit(1)
        repairer = INSVRepairer(args.input, args.output)
        success = repairer.repair_with_reference(args.reference)
    elif args.scan:
        repairer = INSVRepairer(args.input, args.output)
        success = repairer.repair_standalone()
    else:
        print("No repair mode specified. Running diagnosis...")
        print("Use --reference or --scan to repair.\n")
        diag = INSVDiagnoser(args.input)
        findings = diag.diagnose()
        diag.close()
        if findings['repairable']:
            print("This file can be repaired. Run with:")
            print(f"  python3 insv_repair.py {args.input} --scan")
            print(f"  python3 insv_repair.py {args.input} --reference <good_file.insv>")
        return

    if not success:
        print("\nRepair failed.")
        sys.exit(1)

    print("\nVerifying repaired file...")
    try:
        import subprocess
        result = subprocess.run(
            ['ffprobe', '-v', 'error', '-show_entries',
             'stream=index,codec_name,codec_type,width,height,duration,nb_frames',
             '-of', 'compact', repairer.output_path],
            capture_output=True, text=True, timeout=30)
        if result.stdout:
            print("  ffprobe output:")
            for line in result.stdout.strip().split('\n'):
                print(f"    {line}")
        if result.stderr:
            print(f"  ffprobe errors: {result.stderr.strip()}")
    except FileNotFoundError:
        print("  ffprobe not found - install ffmpeg to verify output")
    except Exception as e:
        print(f"  Verification error: {e}")

if __name__ == '__main__':
    main()