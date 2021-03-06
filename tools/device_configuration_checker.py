# coding=utf-8
# Copyright (c) 2016 Intel, Inc.
# Author Erkka Kääriä <erkka.kaaria@intel.com>
# Author Simo Kuusela <simo.kuusela@intel.com>
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
Module for device configuration check functionality.
"""
import os
import copy
from Queue import Queue
from threading import Thread
from multiprocessing import Process
from multiprocessing import Queue as multiprocessing_queue

import aft.config as config
import aft.errors as errors
import aft.devices.common as common
from aft.tester import Tester
from aft.devicesmanager import DevicesManager
from aft.logger import Logger as logger

def check_all(args):
    """
    Checks that configurations are valid for all the devices either fast or
    accurately.

    The difference between these is whether tests are run parallel or serial.
    Parallel testing may cause false positives (everything appears to be Ok)
    when device configurations are mixed. For example, if two devices have
    their power cutter settings mixed (powering on device 1 actually powers
    device 2 and vice versa), everything could appear to be ok during parallel
    testing as both devices would be powered on roughly at the same time.

    Args
        args (configuration object): Program command line arguments

    Returns:
        None
    """

    if not args.topology:
        raise errors.AFTConfigurationError("Topology file must be specified")

    manager = DevicesManager(args)
    configs = manager.get_configs()

    if args.checkall == "fast":
        return check_all_parallel(args, configs)
    elif args.checkall == "accurate":
        return check_all_serial(args, configs)
    else:
        raise errors.AFTConfigurationError("Invalid option " + args.checkall)


def check_all_parallel(args, configs):
    """
    Checks all the devices in parallel

    Args
        args (configuration object): Program command line arguments

        configs (dictionary): Device configurations


    Return:
        Success status (tuple(Boolean, String)):
            Tuple containing the test status. First value is boolean value
            signifying whether tests were run succesfully or not (True/False ->
            Success/Failure). Second parameter contains the result string.
    """

    if args.verbose:
        print("Running parallel configuration check on all devices")

    threads = []

    return_values = Queue()

    def check_wrapper(args, queue):
        """
        Wrapper function for check. Calls check and stores the result in a queue

        Args:
            args (configuration object):
                Process command line arguments, modified for current device

            queue (multiprocessing.Queue):
                Queue used to communicate results back to the main thread.
        """
        ret = check(args)
        queue.put((ret, args.device))


    # IMPORTANT NOTE:
    # Currently (at the time of writing), serial recorder is run in a separate
    # python process, and killed with atexit-handler. These handlers are not
    # called when Process is joined, so Threads must be used instead
    for dev_config in configs:
        device_args = _get_device_args(args, dev_config)

        thread = Thread(
            target=check_wrapper,
            args=(device_args, return_values))
        thread.start()
        threads.append(thread)

    for thread in threads:
        thread.join()


    success = True
    result = ""

    while not return_values.empty():
        item = return_values.get()
        success, result = _handle_result(
            item[0],
            item[1],
            success,
            result)

    return (success, result)


def check_all_serial(args, configs):
    """
    Checks all the devices in serial

    Args
        args (configuration object): Program command line arguments

        configs (dictionary): Device configurations


    Return:
        Success status (tuple(Boolean, String)):
            Tuple containing the test status. First value is boolean value
            signifying whether tests were run succesfully or not (True/False ->
            Success/Failure). Second parameter contains the result string.
    """
    if args.verbose:
        print("Running serial configuration check on all devices")

    success = True
    result = ""

    for dev_config in configs:
        device_args = _get_device_args(args, dev_config)
        success, result = _handle_result(
            check(device_args),
            device_args.device,
            success,
            result)

    return (success, result)

def _get_device_args(args, dev_config):
    """
    Gets necessary device args from dev_config and assigns them to a copy of
    args object.

    Args:
        args (configuration object): Program command line arguments
        dev_config (dictionary): device configurations

    Returns:
        args (configuration object) containing command line parameters, updated
        to match current device.
    """

    device_args = copy.deepcopy(args)
    device_args.device = dev_config["name"]
    # if device has serial_port specified -> record it
    if "serial_port" in dev_config["settings"]:
        device_args.record = True
    else:
        device_args.record = False

    return device_args

def _handle_result(check_result, device, success_status, result_string):
    """
    Takes individual device result and combines it into a single result

    Args:
        check_result (Tuple(Bool, String)):
            The device test result status and string

        device (string): Name of the device that was tested

        success_status (Bool): Current overall success status of the tests

        result_string (String): Current overall result message string

    Returns:
        Tuple (Bool, String) containing current success status and the
        result string.


    """

    success_status = success_status and check_result[0]
    result_string += "\nResults for device " + device + ":\n"
    result_string += check_result[1]
    result_string += "\n\n"

    return (success_status, result_string)

def check(args):
    """
    Checks that the specified device is configured correctly

    Args:
        args (configuration object): Program command line arguments

    Returns:
        Tuple (Bool, String): Test status code and result message string. True
        indicates tests passed succesfully, false indicates that there were
        failures
    """

    if not args.device:
        raise errors.AFTConfigurationError(
            "You must specify the device that will be checked")

    if args.verbose:
        print("Running configuration check on " + args.device)

    if args.checkall:
        logger.init_thread(args.device + "_")

    logger.info("Running configuration check on " + args.device)

    manager = DevicesManager(args)
    device = manager.reserve_specific(args.device)

    if args.verbose:
        print("Device " + args.device + " acquired, running checks")

    try:
        sanity_results = _run_sanity_tests(args, device)
        image_test_results = (True, "Image Test result: Not run")
        # only run image test if sanity test passed
        if sanity_results[0] == True:
            image_test_results = _run_tests_on_know_good_image(args, device)

    finally:
        if args.verbose:
            print("Releasing device " + args.device)

        if not args.nopoweroff:
            device.detach()

        manager.release(device)

    results = (
        sanity_results[0] and image_test_results[0],
        sanity_results[1] + "\n" + image_test_results[1]
        )

    if not results[0]:
        common.blacklist_device(
                device.dev_id,
                args.device,
                "Failed device health check")
        msg = "Device " + args.device + " failed health test - blacklisting"
        logger.info(msg)
        if args.verbose:
            print msg

    return results

def _run_sanity_tests(args, device):
    """
    Run basic sanity tests on the device and return the result

    Args:
        args (configuration object): Program command line arguments
        device (aft.Device) : The device that is being tested

    Returns:
        Dictionary containing test result tuples(Bool, String). Boolean value
        indicates if test was successful or not (True/False), string contains
        explanation.
    """
    if args.record:
        if args.verbose:
            print("Serial recording enabled on " + args.device)

        device.parameters["serial_log_name"] = args.device + "_serial.log"
        device.record_serial()
    else:
        if args.verbose:
            print("Serial recording disabled on " + args.device)


    poweron_status = (True, "Ok")
    connection_status = (True, "Ok")
    poweroff_status = (True, "Ok")
    serial_status = (True, "Ok")

    try:
        if args.verbose:
            print("Running power on test on " + args.device)
        device.check_poweron()
    except KeyboardInterrupt:
        raise
    except errors.AFTNotImplementedError as error:
        poweron_status = (True, str(error))
    except Exception as error:
        poweron_status = (False, str(error))

    try:
        if args.verbose:
            print("Running connection test on " + args.device)
        device.check_connection()
    except KeyboardInterrupt:
        raise
    except errors.AFTNotImplementedError as error:
        connection_status = (True, str(error))
    except Exception as error:
        connection_status = (False, str(error))

    try:
        if args.verbose:
            print("Running power off test on " + args.device)
        device.check_poweroff()
    except KeyboardInterrupt:
        raise
    except errors.AFTNotImplementedError as error:
        poweroff_status = (True, str(error))
    except Exception as error:
        poweroff_status = (False, str(error))

    return _format_sanity_test_result(args, device, {
        "poweron_status": poweron_status,
        "connection_status": connection_status,
        "poweroff_status": poweroff_status,
        "serial_status": serial_status
    })

def _format_sanity_test_result(args, device, test_results):
    """

    Args:
        args (configuration object): Program command line arguments
        device (aft.Device): The device that is being tested
        test_results (dictionary): The test results of the individual tests

    Returns:
        Tuple(Bool, String) containing the overall status of the tests. Boolean
        value indicates if there were failures (False if test or tests failed).
        String contains the overall test result string
    """
    poweron_status = test_results["poweron_status"]
    connection_status = test_results["connection_status"]
    poweroff_status = test_results["poweroff_status"]
    serial_status = test_results["serial_status"]

    if args.record:
        if not os.path.isfile(device.parameters["serial_log_name"]):
            serial_status = (False, "No serial log file was generated")
        else:
            stats = os.stat(device.parameters["serial_log_name"])
            # this is mostly a heuristic approach to eliminate few newlines
            # and other whitespace characters
            # TODO\FIXME: Actually open log file and strip
            # whitespace to get more accurate file size
            if stats.st_size < 5:
                serial_status = (False, "Serial log file seems to be empty")
    else:
        serial_status = (True, "Skipped - serial recording is off")


    result = "Configuration test result: "
    result += "\n\tPower on test: " + poweron_status[1]
    result += "\n\tConnection test: " + connection_status[1]
    result += "\n\tPower off test: " + poweroff_status[1]
    result += "\n\tSerial test: " + serial_status[1]


    if poweron_status[0] == True and poweroff_status[0] == False:
        result += ("\n\n\tNote: Power on test succeeding and power off test "
                   "failing might indicate that power cutter settings are "
                   "incorrect (for example: Two devices may have power cutter "
                   "settings inverted)")
    elif connection_status[0] == True and poweroff_status[0] == False:
        result += ("\n\n\tNote: Connection test succeeding and power off test "
                   "failing might indicate that power cutter settings are "
                   "incorrect (for example: Two devices may have power cutter "
                   "settings inverted)")

    if poweron_status[0] == False and poweroff_status[0] == True:
        result += ("\n\n\tNote: Power off test status might be invalid as "
                   "the device failed the power on test (device may have been"
                   " off the whole time)")


    success = poweron_status[0] and connection_status[0] and \
              poweroff_status[0] and serial_status[0]

    return (success, result)


def _run_tests_on_know_good_image(args, device):

    if device.model.lower() == "edison":
        return (True, "Skipped - produces too many false negatives")

    if args.verbose:
        print("Flashing and testing a known good image")

    logger.info("Flashing and testing a known good image")

    image_directory_path = os.path.join(
        config.KNOWN_GOOD_IMAGE_FOLDER,
        device.model.lower())


    image = None
    if device.model.lower() == "beagleboneblack":
        image = image_directory_path
    elif device.model.lower() == "edison":
        image = os.path.join(
            image_directory_path,
            "ostro-image-edison.ext4")
    else:
        image = os.path.join(
            image_directory_path,
            "good-image.dsk")

    if args.verbose:
        print("Image file: " + str(image))
        print("Flashing " + str(device.name))

    logger.info("Image file: " + str(image))

    try:

        results_queue = multiprocessing_queue()

        def worker(results):

            os.chdir(os.path.join(image_directory_path, "iottest"))
            # nuke test logs
            for f in os.listdir("."):
                if "ssh_target_log" in f:
                    os.remove(f)

            os.chdir(image_directory_path)
            ## Remove all log and xml files from previous run to prevent clutter
            #for f in os.listdir("."):
            #    if f.endswith(".log") or f.endswith(".xml"):
            #        os.remove(f)


            device.write_image(image)

            tester = Tester(device)
            tester.execute()
            results.put((tester.get_results(), tester.get_results_str()))

        # Run this in a separate process, so that we can change its working
        # directory, without changing the working directory for the rest of
        # the program
        process = Process(
            target=worker,
            args=(results_queue,))

        process.start()
        process.join()

        if results_queue.empty():
            raise errors.AFTDeviceError("No results from test run")

        results = results_queue.get()
        result = reduce(lambda x, y: x and y, results[0])

        result_str = "Image test result: "


        if result:
            result_str += "Ok"
        else:
            result_str += " Failure(s): \n\n" + results[1]

        return (result, result_str)

    except Exception as error:
        return (False, "Image Test result: " + str(error))
