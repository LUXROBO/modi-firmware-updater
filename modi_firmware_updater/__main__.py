import os
import sys
import time

from getopt import getopt, GetoptError

from modi_firmware_updater.core.stm32_updater import STM32FirmwareUpdater
from modi_firmware_updater.core.esp32_updater import ESP32FirmwareUpdater
from modi_firmware_updater.core.gd32_updater import GD32FirmwareUpdater


def check_option(*options):
    for o, a in opts:
        if o in options:
            return a if a else True
    return False


if __name__ == '__main__':
    try:
        # All commands should be defined here in advance
        opts, args = getopt(
            sys.argv[1:], 'nbm',
            ['update_network', 'update_network_base', 'update_modules']
        )
    # Exit program if an invalid option has been entered
    except GetoptError as err:
        print(str(err))
        print(usage)
        os._exit(2)

    # Ensure that there is an option but argument
    if len(sys.argv) == 1 or len(args) > 0:
        print(usage)
        os._exit(2)

    # Update ESP32 module (only network module)
    if check_option('-n', '--update_network'):
        init_time = time.time()
        updater = ESP32FirmwareUpdater()
        updater.update_firmware()
        fin_time = time.time()
        print(f'Took {fin_time - init_time:.2f} seconds to update :)')
        os._exit(0)

    # Update STM32 base (of network module)
    if check_option('-b', '--update_network_base'):
        init_time = time.time()
        updater = STM32FirmwareUpdater()
        updater.update_module_firmware(update_network_base=True)
        fin_time = time.time()
        print(f'Took {fin_time - init_time:.2f} seconds to update')
        os._exit(0)

    # Update MODI STM32 modules (every modules but network module)
    if check_option('-m', '--update_modules'):
        init_time = time.time()
        updater = STM32FirmwareUpdater()
        updater.update_module_firmware()
        fin_time = time.time()
        print(f'Took {fin_time - init_time:.2f} seconds to update')
        os._exit(0)

