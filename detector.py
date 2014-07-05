import os
import time
import logging
from volatility import session
from volatility import plugins
from volatility import utils

import messages
from messages import *
from abstracts import DetectorError
from config import Config
from service import Service
from utils import get_resource

log = logging.getLogger("detector")
log.propagate = 0
log.addHandler(logging.FileHandler(os.path.join(os.getcwd(), 'detector.log')))
log.setLevel(logging.INFO)

def scan(service_path, profile_name, queue_results):
    # Initialize Volatility session and specify path to the winpmem service
    # and the detected profile name.
    sess = session.Session(filename=service_path, profile=profile_name)

    # Find Yara signatures, if file is not available, we need to terminate.
    yara_path = get_resource(os.path.join('rules', 'signatures.yar'))
    if not os.path.exists(yara_path):
        raise DetectorError("Unable to find signatures file!")

    # Load the yarascan plugin from Volatility. We pass it the index file which
    # is used to load the different rulesets.
    yara_plugin = sess.plugins.yarascan(yara_file=yara_path)

    # This ia a list used to track which rule gets matched. I'm going to
    # store the details for each unique rule only.
    matched = []
    # Initialize memory scanner and loop through matches.
    for rule, address, _, value in yara_plugin.generate_hits(sess.physical_address_space):
        # If the current matched rule was not observed before, log detailed
        # information and a dump of memory in the proximity.
        if not rule in matched:
            # Add the name of the rule to the matched list.
            matched.append(rule)

            # Obtain proximity dump.
            context = sess.physical_address_space.zread(address-0x10, 0x40)

            rule_data = ''
            for offset, hexdata, translated_data in utils.Hexdump(context):
                rule_data += '{0} {1}\n'.format(hexdata, ''.join(translated_data))

            log.warning("Matched: %s [0x%.08x]: %s\n\n%s", rule, address, value, rule_data)

            # Add match to the resuts queue.
            queue_results.put({'rule' : rule, 'address' : address, 'value' : value})

    # If any rule gets matched, we need to notify the user and instruct him
    # on how to proceed from here.
    if len(matched) > 0:
        return True
    else:
        return False

def main(queue_results, queue_errors):
    # Generate configuration values.
    cfg = Config()

    # Check if this is a supported version of Windows and if so, obtain the
    # volatility profile name.
    if not cfg.get_profile_name():
        queue_errors.put(messages.UNSUPPORTED_WINDOWS)
        return

    # Obtain the path to the driver to load. At this point, this check should
    # not fail, but you never know.
    if not cfg.get_driver_path():
        queue_errors.put(messages.NO_DRIVER)
        return

    log.info("Selected Driver: {0}".format(cfg.driver))
    log.info("Selected Profile Name: {0}".format(cfg.profile))

    # Initialize the winpmem service.
    try:
        service = Service(driver=cfg.driver, service=cfg.service_name)
        service.create()
        service.start()
    except DetectorError as e:
        log.critical(e)
        queue_errors.put(messages.SERVICE_NO_START)
        return
    else:
        log.info("Service started")

    # Launch the scanner.
    try:
        scan(cfg.service_path, cfg.profile, queue_results)
    except DetectorError as e:
        log.critical(e)
        queue_errors.put(messages.SCAN_FAILED)
    else:
        log.info("Scanning finished")

    # Stop the winpmem service and unload the driver. At this point we should
    # have cleaned up everything left on the system.
    try:
        service.stop()
        service.delete()
    except DetectorError as e:
        log.critical(e)
        queue_errors.put(messages.SERVICE_NO_STOP)
    else:
        log.info("Service stopped")

    log.info("Analysis finished")
