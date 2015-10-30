# Copyright (c) 2015 Intel, Inc.
# Author igor.stoppa@intel.com & topi.kuutela@intel.com
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the
# Free Software Foundation; version 2 of the License
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
# See the GNU General Public License for more details.

"""
Main entry point for aft.
"""

import sys
import logging
import argparse
from aft.devicesmanager import DevicesManager
from aft.tester import Tester

def main(argv=None):
    """
    Entry point for library-like use.
    """
    logging.basicConfig(filename='aft.log', level=logging.DEBUG,
                        format='%(asctime)s - %(name)s - '
                               '%(levelname)s - %(message)s')

    if argv != None:
        backup_argv = sys.argv
        sys.argv = argv

    args = parse_args()
    device_manager = DevicesManager(args)
    device = device_manager.reserve()
    tester = Tester(device)
    if not args.noflash:
        device.write_image(args.file_name)
    if not args.notest:
        tester.execute()
    if not args.nopoweroff:
        device.detach()

    if "backup_argv" in locals():
        sys.argv = backup_argv
    return 0

def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", action = "store",
                        help = "Configuration file describing the supported device types",
                        default = "/etc/aft/config/catalog.cfg")
    parser.add_argument("--topology", action = "store",
                        help = "Configuration file describing the (physically) attached devices",
                        default = "/etc/aft/config/topology.cfg")
    parser.add_argument("file_name", action = "store",
                        help = "Image to write: a local file, "
                             "compatible with the supported platforms.")
    parser.add_argument("machine", action = "store", help = "Model type")
    parser.add_argument("--noflash", action = "store_true", default = False,
                        help = "Skip device flashing")
    parser.add_argument("--notest", action = "store_true", default = False,
                        help = "Skip test case execution (still creates a test plan)")
    parser.add_argument("--nopoweroff", action = "store_true", default = False,
                        help = "Do not power off the DUT after testing")
    return parser.parse_args()

if __name__ == "__main__":
    sys.exit(main())
