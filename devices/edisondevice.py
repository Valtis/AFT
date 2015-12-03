# Copyright (c) 2013-2015 Intel, Inc.
# Author Topi Kuutela <topi.kuutela@intel.com>
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
Class representing a DUT which can be flashed from the testing harness and
can get an IP-address.
"""

import os
import sys
import logging
import subprocess32
import time
import netifaces
import shutil
import random

from aft.device import Device
import aft.tools.ssh as ssh


def _make_directory(directory):
    """
    Make directory safely
    """
    try:
        os.makedirs(directory)
    except OSError:
        if not os.path.isdir(directory):
            raise


def _get_nth_parent_dir(path, parent):
    """
    Return 'parnet'h parent directory of 'path'
    """
    if parent == 0:
        return path
    return _get_nth_parent_dir(os.path.dirname(path), parent - 1)


def _log_subprocess32_error(err):
    """
    Log subprocess32 error cleanly
    """
    logging.critical(str(err.cmd) + "failed with error code: " +
                     str(err.returncode) + " and output: " + str(err.output))
    logging.critical("Aborting")
    sys.exit(1)

# pylint: disable=too-many-instance-attributes


class EdisonDevice(Device):
    """
    AFT-device for Edison
    """

    _LOCAL_MOUNT_DIR = "edison_root_mount"
    _EDISON_DEV_ID = "8087:0a99"
    _DUT_USB_SERVICE_FILE = "usb-network.service"
    _DUT_USB_SERVICE_LOCATION = "etc/systemd/system"
    _DUT_USB_SERVICE_CONFIG_FILE = "usb-network"
    _DUT_USB_SERVICE_CONFIG_DIR = "etc/conf.d"
    _DUT_CONNMAN_SERVICE_FILE = "lib/systemd/system/connman.service"
    _MODULE_DATA_PATH = os.path.join(os.path.dirname(__file__), 'data')
    _FLASHER_OUTPUT_LOG = "flash.log"

    def __init__(self, parameters, channel):
        super(EdisonDevice, self).__init__(device_descriptor=parameters,
                                           channel=channel)
        self._configuration = parameters

        self._usb_path = self._configuration["edison_usb_port"]
        subnet_parts = self._configuration[
            "network_subnet"].split(".")  # always *.*.*.*/30
        ip_range = ".".join(subnet_parts[0:3])
        self._gateway_ip = ".".join([ip_range, str(int(subnet_parts[3]) + 0)])
        self._host_ip = ".".join([ip_range, str(int(subnet_parts[3]) + 1)])
        self._dut_ip = ".".join([ip_range, str(int(subnet_parts[3]) + 2)])
        self._broadcast_ip = ".".join(
            [ip_range, str(int(subnet_parts[3]) + 3)])
        self._root_extension = "ext4"

    def write_image(self, file_name):
        file_no_extension = os.path.splitext(file_name)[0]
        self._mount_local(file_no_extension)
        self._add_usb_networking()
        self._add_ssh_key()
        self._unmount_local()

        # self._recovery_flash() # Disabled for now. Concurrency issue. Should
        # lock the xfstk util.

        # self._flashing_attempts = 0 # dfu-util may occasionally fail. Extra
        # attempts could be used?
        logging.info("Executing flashing sequence.")
        return self._flash_image()

    def _mount_local(self, file_name):
        """
        Mount a image-file to a class-defined folder.
        """
        logging.info("Mounting the root partition for ssh-key and USB-networking " +
                     "service injection.")
        try:
            _make_directory(self._LOCAL_MOUNT_DIR)
            root_file_system_file = file_name + "." + self._root_extension
            subprocess32.check_call(
                ["mount", root_file_system_file, self._LOCAL_MOUNT_DIR])
        except subprocess32.CalledProcessError as err:
            logging.debug("Failed to mount. Is AFT run as root?")
            _log_subprocess32_error(err)

    def _add_usb_networking(self):
        """
        Inject USB-networking service files
        """
        logging.info("Injecting USB-networking service.")
        source_file = os.path.join(self._MODULE_DATA_PATH,
                                   self._DUT_USB_SERVICE_FILE)
        target_file = os.path.join(os.curdir,
                                   self._LOCAL_MOUNT_DIR,
                                   self._DUT_USB_SERVICE_LOCATION,
                                   self._DUT_USB_SERVICE_FILE)
        shutil.copy(source_file, target_file)

        # Copy UID and GID
        source_stat = os.stat(source_file)
        os.chown(target_file, source_stat.st_uid, source_stat.st_gid)

        # Set symlink to start the service at the end of boot
        try:
            os.symlink(os.path.join(os.sep,
                                    self._DUT_USB_SERVICE_LOCATION,
                                    self._DUT_USB_SERVICE_FILE),
                       os.path.join(os.curdir,
                                    self._LOCAL_MOUNT_DIR,
                                    self._DUT_USB_SERVICE_LOCATION,
                                    "multi-user.target.wants",
                                    self._DUT_USB_SERVICE_FILE))
        except OSError as err:
            if err.errno == 17:
                logging.critical("The image file was not replaced. USB-networking service " +
                                 "already exists.")
                print "The image file was not replaced! The symlink for usb-networking " + \
                    "service already exists."
                # print "Aborting."
                # sys.exit(1)
            else:
                raise err

        # Create the service configuration file
        config_directory = os.path.join(os.curdir,
                                        self._LOCAL_MOUNT_DIR,
                                        self._DUT_USB_SERVICE_CONFIG_DIR)
        _make_directory(config_directory)
        config_file = os.path.join(config_directory,
                                   self._DUT_USB_SERVICE_CONFIG_FILE)

        # Service configuration options
        config_stream = open(config_file, 'w')
        config_options = ["Interface=usb0",
                          "Address=" + self._dut_ip,
                          "MaskSize=30",
                          "Broadcast=" + self._broadcast_ip,
                          "Gateway=" + self._gateway_ip]
        for line in config_options:
            config_stream.write(line + "\n")
        config_stream.close()

        # Ignore usb0 in connman
        original_connman = os.path.join(os.curdir,
                                        self._LOCAL_MOUNT_DIR,
                                        self._DUT_CONNMAN_SERVICE_FILE)
        output_file = os.path.join(os.curdir,
                                   self._LOCAL_MOUNT_DIR,
                                   self._DUT_CONNMAN_SERVICE_FILE + "_temp")
        connman_in = open(original_connman, "r")
        connman_out = open(output_file, "w")
        for line in connman_in:
            if "ExecStart=/usr/sbin/connmand" in line:
                line = line[0:-1] + " -I usb0 \n"
            connman_out.write(line)
        connman_in.close()
        connman_out.close()
        shutil.copy(output_file, original_connman)
        os.remove(output_file)

    _HARNESS_AUTHORIZED_KEYS_FILE = "authorized_keys"

    def _add_ssh_key(self):
        """
        Inject the ssh-key to DUT's authorized_keys
        """
        logging.info("Injecting ssh-key.")
        source_file = os.path.join(self._MODULE_DATA_PATH,
                                   self._HARNESS_AUTHORIZED_KEYS_FILE)
        ssh_directory = os.path.join(os.curdir,
                                     self._LOCAL_MOUNT_DIR,
                                     "home", "root", ".ssh")
        authorized_keys_file = os.path.join(os.curdir,
                                            ssh_directory,
                                            "authorized_keys")
        _make_directory(ssh_directory)
        shutil.copy(source_file, authorized_keys_file)
        os.chown(ssh_directory, 0, 0)
        os.chown(authorized_keys_file, 0, 0)
        # Note: incompatibility with Python 3 in chmod octal numbers
        os.chmod(ssh_directory, 0700)
        os.chmod(authorized_keys_file, 0600)

    def _unmount_local(self):
        """
        Unmount the previously mounted image from class-defined folder
        """
        logging.info("Flushing and unmounting the root filesystem.")
        try:
            subprocess32.check_call(["sync"])
            subprocess32.check_call(["umount", os.path.join(os.curdir,
                                                            self._LOCAL_MOUNT_DIR)])
        except subprocess32.CalledProcessError as err:
            _log_subprocess32_error(err)

    def _reboot_device(self):
        """
        Reboot the DUT
        """
        # .call(["cutter_on_off", self._cutter_dev_path, "0"])
        self.channel.disconnect()
        time.sleep(1)
        # .call(["cutter_on_off", self._cutter_dev_path, "1"])
        self.channel.connect()

    def _recovery_flash(self):
        """
        Execute the flashing of device-side DFU-tools
        """
        logging.info("Recovery flashing.")
        try:
            # This can cause race condition if multiple devices are booted at
            # the same time!
            attempts = 0
            xfstk_parameters = ["xfstk-dldr-solo",
                                "--gpflags", "0x80000007",
                                "--osimage", "u-boot-edison.img",
                                "--fwdnx", "edison_dnx_fwr.bin",
                                "--fwimage", "edison_ifwi-dbg-00.bin",
                                "--osdnx", "edison_dnx_osr.bin"]
            self._reboot_device()
            while subprocess32.call(xfstk_parameters) and attempts < 10:
                logging.info(
                    "Rebooting and trying recovery flashing again. " + str(attempts))
                self._reboot_device()
                time.sleep(random.randint(10, 30))
                attempts += 1

        except subprocess32.CalledProcessError as err:
            _log_subprocess32_error(err)
        except OSError as err:
            logging.critical("Failed recovery flashing, errno = " +
                             str(err.errno) + ". Is the xFSTK tool installed?")
            sys.exit(1)

    def _wait_for_device(self, timeout=15):
        """
        Wait until the testing harness detects the Edison after boot
        """
        start = time.time()
        while time.time() - start < timeout:
            output = subprocess32.check_output(
                ["dfu-util", "-l", "-d", self._EDISON_DEV_ID])
            output_lines = output.split("\n")
            fitting_lines = [
                line for line in output_lines if 'path="' + self._usb_path + '"' in line]
            if fitting_lines:
                return
            else:
                continue
        raise IOError("Could not find the device in DFU-mode in " +
                      str(timeout) + " seconds.")
# pylint: disable=dangerous-default-value

    def _dfu_call(self, alt, source, extras=[], attempts=4, timeout=600):
        """
        Call DFU-util successively with arguments until it succeeds
        """
        flashing_log_file = open(self._FLASHER_OUTPUT_LOG, "a")
        attempt = 0
        while attempt < attempts:
            self._wait_for_device()
            execution = subprocess32.Popen(["dfu-util", "-v", "--path", self._usb_path,
                                            "--alt", alt, "-D", source] + extras,
                                           stdout=flashing_log_file,
                                           stderr=flashing_log_file)
            start = time.time()
            while time.time() - start < timeout:
                status = execution.poll()
                if status == None:
                    continue
                else:
                    flashing_log_file.close()
                    return

            try:
                execution.kill()
            except OSError as err:
                if err.errno == 3:
                    pass
                else:
                    raise
            attempt += 1

            logging.warning("Flashing failed on alt " + alt + " for file " + source +
                            " on USB-path " + self._usb_path +
                            ". Rebooting and attempting again for " +
                            str(attempt) + "/" + str(attempts) + " time.")
            self._reboot_device()
        flashing_log_file.close()
        raise IOError("Flashing failed 4 times. Raising error (aborting).")
# pylint: enable=dangerous-default-value

    IFWI_DFU_FILE = "edison_ifwi-dbg"

    def _flash_image(self):
        """
        Execute the sequence of DFU-calls to flash the image.
        """
        self._reboot_device()
        logging.info("Flashing IFWI.")
        for i in range(0, 7):
            stri = str(i)
            self._dfu_call("ifwi0" + stri, self.IFWI_DFU_FILE +
                           "-0" + stri + "-dfu.bin")
            self._dfu_call("ifwib0" + stri, self.IFWI_DFU_FILE +
                           "-0" + stri + "-dfu.bin")

        logging.info("Flashing u-boot")
        self._dfu_call("u-boot0", "u-boot-edison.bin")
        self._dfu_call("u-boot-env0", "u-boot-envs/edison-blankcdc.bin")
        self._dfu_call(
            "u-boot-env1", "u-boot-envs/edison-blankcdc.bin", ["-R"])
        self._wait_for_device()

        logging.info("Flashing boot partition.")
        self._dfu_call("boot", "iot-os-image-edison." +
                       self._configuration["boot_extension"])
        logging.info("Flashing update partition.")
        self._dfu_call("update", "iot-os-image-edison." +
                       self._configuration["recovery_extension"])
        logging.info("Flashing root partition.")
        self._dfu_call("rootfs", "iot-os-image-edison." +
                       self._configuration["root_extension"], ["-R"])
        logging.info("Flashing complete.")
        return True

    def test(self, test_case):
        self.open_interface()
        enabler = subprocess32.Popen(["python",
                                      os.path.join(os.path.dirname(__file__),
                                                   os.path.pardir, "tools",
                                                   "nicenabler.py"),
                                      self._usb_path, self._host_ip + "/30"])
        self._wait_until_ssh_visible()
        tester_result = test_case.run(self)
        enabler.kill()
        return tester_result

    def execute(self, command, timeout, user="root", verbose=False):
        pass

    def push(self, local_file, remote_file, user="root"):
        pass

    def open_interface(self):
        """
        Open the host's network interface for testing
        """
        interface = self._get_usb_nic()
        ip_subnet = self._host_ip + "/30"
        logging.info("Opening the host network interface for testing.")
        subprocess32.check_call(["ifconfig", interface, "up"])
        subprocess32.check_call(["ifconfig", interface, ip_subnet])

    def _wait_until_ssh_visible(self, timeout=180):
        """
        Wait until the DUT answers to ssh
        """
        start = time.time()
        while time.time() - start < timeout:
            if ssh.test_ssh_connectivity(self._dut_ip):
                return
        logging.critical("Failed to establish ssh-connection in " +
                         str(timeout) + " seconds after enabling the network interface.")
        raise IOError("Failed to establish ssh-connection in " +
                      str(timeout) + " seconds after enabling the network interface.")

    def get_ip(self):
        return self._dut_ip

    _NIC_FILESYSTEM_LOCATION = "/sys/class/net"

    def _get_usb_nic(self, timeout=120):
        """
        Search and return for the network interface attached to the DUT's USB-path
        """
        logging.info(
            "Searching for the host network interface from usb path " + self._usb_path)
        start = time.time()
        while time.time() - start < timeout:

            interfaces = netifaces.interfaces()
            for interface in interfaces:
                try:
                    # Test if the interface is the correct USB-ethernet NIC
                    nic_path = os.path.realpath(os.path.join(
                        self._NIC_FILESYSTEM_LOCATION, interface))
                    usb_path = _get_nth_parent_dir(nic_path, 3)

                    if os.path.basename(usb_path) == self._usb_path:
                        return interface
                except IOError as err:
                    print "IOError: " + str(err.errno) + " " + err.message
                    print "Error likely caused by jittering network interface. Ignoring."
                    logging.warning("An IOError occured when testing network interfaces. " +
                                    " IOERROR: " + str(err.errno) + " " + err.message)
            time.sleep(1)

        raise ValueError("Could not find a network interface from USB-path " +
                         self._usb_path + " in 120 seconds.")
# pylint: enable=too-many-instance-attributes
