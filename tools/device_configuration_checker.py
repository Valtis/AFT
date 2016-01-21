import os
import logging
from multiprocessing import Process, Queue
import ConfigParser
import aft.errors as errors
import aft.config as config
from aft.devicesmanager import DevicesManager

def check_all(args):
    """
    Checks all the devices either fast or accurately.

    The difference between these is whether tests are run parallel or serial.
    Parallel testing may cause false negatives (everything appears to be Ok)
    when device configurations are mixed. For example, if two devices have
    their power cutter settings mixed (powering on device 1 actually powers
    device 2 and vice versa), everything would appear to be ok during parallel
    testing as both devices would be powered on roughly at the same time.
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
    processes = []

    return_values = Queue()

    def check_wrapper(args, queue):
        ret = check(args)
        queue.put((ret, args.device))

    for config in configs:
        device_args = get_device_args(args, config)

        process = Process(target=check_wrapper, args=(device_args, return_values))
        process.start()
        processes.append(process)

    for process in processes:
        process.join()


    success = True
    result = ""

    while not return_values.empty():
        item = return_values.get()
        success, result = handle_result(
            item[0],
            item[1],
            success,
            result)

    return (success, result)


def check_all_serial(args, configs):
    success = True
    result = ""

    for config in configs:
        device_args = get_device_args(args, config)
        success, result = handle_result(
            check(device_args),
            device_args.device,
            success,
            result)

    return (success, result)

def get_device_args(args, config):
    device_args = args
    device_args.device = config["name"]
    # heuristic for enabling recording: if device has serial_port specified
    # -> record it
    if "serial_port" in config["settings"]:
        device_args.record = True
    else:
        device_args.record = False

    return device_args

def handle_result(check_result, device, success_status, result_string):

    success_status = success_status and check_result[0]
    result_string += "\nResults for device " + device + ":\n"
    result_string += check_result[1]
    result_string += "\n\n"

    return (success_status, result_string)

def check(args):

    if not args.device:
        raise errors.AFTConfigurationError(
            "You must specify the device that will be checked")


    print "Running configuration check on " + args.device
    logging.info("Running configuration check on " + args.device)
    manager = DevicesManager(args)

    device = manager.reserve_specific(args.device)
    print "Device " + args.device + " acquired, running checks"

    if args.record:
        device.parameters["serial_log_name"] = args.device + "_serial.log"
        device.record_serial()

    poweron_status = (True, "Ok")
    connection_status = (True, "Ok")
    poweroff_status = (True, "Ok")
    serial_status = (True, "Ok")

    try:
        device.check_poweron()
    except KeyboardInterrupt:
        raise
    except errors.AFTNotImplementedError, error:
        poweron_status = (True, str(error))
    except Exception, error:
        poweron_status = (False, str(error))

    try:
        device.check_connection()
        pass
    except KeyboardInterrupt:
        raise
    except errors.AFTNotImplementedError, error:
        connection_status = (True, str(error))
    except Exception, error:
        connection_status = (False, str(error))

    try:
        device.check_poweroff()
        pass
    except KeyboardInterrupt:
        raise
    except errors.AFTNotImplementedError, error:
        poweroff_status = (True, str(error))
    except Exception, error:
        poweroff_status = (False, str(error))

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


    print "Releasing device " + args.device
    manager.release(device)

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


    success = poweron_status[0] and connection_status[0] and poweroff_status[0] and serial_status[0]

    return (success, result)










