import json
import sys
import threading as th
import time
from io import open
from os import path
from itertools import zip_longest

import serial
import serial.tools.list_ports as stl

from modi_firmware_updater.util.connection_util import list_modi_ports
from modi_firmware_updater.util.message_util import (parse_message, unpack_data)
from modi_firmware_updater.util.module_util import (Module, get_module_type_from_uuid)


def retry(exception_to_catch):
    def decorator(func):
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except exception_to_catch:
                return wrapper(*args, **kwargs)

        return wrapper

    return decorator


class STM32FirmwareUpdater(serial.Serial):
    """STM32 Firmware Updater: Updates a firmware of given module"""

    NO_ERROR = 0
    UPDATE_READY = 1
    WRITE_FAIL = 2
    VERIFY_FAIL = 3
    CRC_ERROR = 4
    CRC_COMPLETE = 5
    ERASE_ERROR = 6
    ERASE_COMPLETE = 7

    def __init__(self, device=None):
        self.print = True
        if device != None:
            super().__init__(
                device, timeout = 0.1, baudrate = 921600
            )
        else:
            modi_ports = list_modi_ports()
            if not modi_ports:
                raise serial.SerialException("No MODI port is connected")
            for modi_port in modi_ports:
                try:
                    super().__init__(
                        modi_port.device, timeout=0.1, baudrate=921600
                    )
                except Exception:
                    self.__print('Next network module')
                    continue
                else:
                    break
            self.__print(f"Connecting to MODI network module at {modi_port.device}")

        self.network_version = None
        self.network_uuid = None
        self.network_id = None

        self.update_in_progress = False
        self.ui = None

        self.progress = 0
        self.module_type = None
        self.modules_to_update = []
        self.modules_updated = []
        self.modules_find = False

        self.raise_error_message = True
        self.update_error = 0
        self.update_error_message = ""

        for device in stl.comports():
            if self.name == device.name:
                self.location = device.location
                break

    def set_ui(self, ui):
        self.ui = ui

    def set_print(self, print):
        self.print = print

    def set_raise_error(self, raise_error_message):
        self.raise_error_message = raise_error_message

    def get_network_info(self):
        timeout = 3
        init_time = time.time()
        while True:
            self.__print("request uuid")
            self.send_request_network_uuid()
            self.__print("wait for request")
            recved = self.wait_for_json(timeout)

            if time.time() - init_time > timeout or not recved:
                return None, None

            try:
                json_msg = json.loads(recved)
                if json_msg["c"] == 0x05:
                    unpacked_data = unpack_data(json_msg["b"], (6, 2))
                    module_uuid = unpacked_data[0]
                    module_version_digits = unpacked_data[1]
                    module_type = get_module_type_from_uuid(module_uuid)
                    if module_type == "network":
                        module_version = [
                            str((module_version_digits & 0xE000) >> 13),  # major
                            str((module_version_digits & 0x1F00) >> 8),  # minor
                            str(module_version_digits & 0x00FF)   # patch
                        ]
                        return module_uuid , ".".join(module_version)
                elif json_msg["c"] == 0x0A:
                    module_uuid = unpack_data(json_msg["b"], (6, 2))[0]
                    module_type = get_module_type_from_uuid(module_uuid)
                    if module_type == "network":
                        return module_uuid , None
            except json.decoder.JSONDecodeError as jde:
                self.__print("json parse error: " + str(jde))

            time.sleep(0.2)

    def send_request_network_uuid(self):
        send_pkt = parse_message(0x28, 0xFFF, 0xFFF, (0xFF, 0xFF))
        if self.is_open:
            self.write(send_pkt.encode("utf8"))
            self.reset_input_buffer()

    def send_set_network_module_state(self, did, module_state, pnp_state):
        send_pkt = parse_message(0xA4, 0, did, (module_state, pnp_state))
        if self.is_open:
            self.write(send_pkt.encode("utf8"))
            self.reset_input_buffer()

    def send_set_module_state(self, did, module_state, pnp_state):
        send_pkt = parse_message(0x09, 0, did, (module_state, pnp_state))
        if self.is_open:
            self.write(send_pkt.encode("utf8"))
            self.reset_input_buffer()

    def send_firmware_command(self, operation_type, module_id, crc_val, page_addr):
        rot_scmd = 2 if operation_type == "erase" else 1

        cmd = 0x0D

        """ SID is 12-bits length in MODI CAN.
            To fully utilize its capacity, we split 12-bits into 4 and 8 bits.
            First 4 bits include rot_scmd information.
            And the remaining bits represent rot_stype.
        """
        sid = (rot_scmd << 8) | 1
        did = module_id

        """ The firmware command data to be sent is 8-bytes length.
            Where the first 4 bytes consist of CRC-32 information.
            Last 4 bytes represent page address information.
        """
        crc32_and_page_addr_data = bytearray(8)
        for i in range(4):
            crc32_and_page_addr_data[i] = crc_val & 0xFF
            crc_val >>= 8
            crc32_and_page_addr_data[4 + i] = page_addr & 0xFF
            page_addr >>= 8
        data = crc32_and_page_addr_data

        send_pkt = parse_message(cmd, sid, did, data)
        if self.is_open:
            self.write(send_pkt.encode("utf8"))
            self.reset_input_buffer()

    def receive_firmware_command_response(self, delay = 0.001, timeout = 5):
        response_wait_time = time.time()
        while True:
            responese_success = False
            response_error = False

            recved = self.wait_for_json(timeout)

            if time.time() - response_wait_time > timeout or not recved:
                return False

            try:
                json_msg = json.loads(recved)
                if json_msg["c"] == 0x0C:
                    message_decoded = unpack_data(json_msg["b"], (4, 1))
                    stream_state = message_decoded[1]
                    if stream_state == self.CRC_ERROR or stream_state == self.ERASE_ERROR:
                        response_error = True
                    elif stream_state == self.CRC_COMPLETE or stream_state == self.ERASE_COMPLETE:
                        responese_success = True
            except json.decoder.JSONDecodeError as jde:
                self.__print("json parse error: " + str(jde))

            if responese_success:
                return True

            if response_error:
                return False

            time.sleep(delay)

    def send_firmware_data(self, module_id, seq_num, bin_data):
        cmd = 0x0B
        sid = seq_num
        did = module_id
        data = bytes(bin_data)
        send_pkt = parse_message(cmd, sid, did, data)
        if self.is_open:
            self.write(send_pkt.encode("utf8"))
            self.reset_input_buffer()

    def set_firmware_command(self, oper_type, module_id, crc_val, page_addr):
        self.send_firmware_command(oper_type, module_id, crc_val, page_addr)
        ret = self.receive_firmware_command_response()
        if not ret and oper_type == "erase":
            retry_count = 0
            max_retry = 5
            while not ret:
                self.send_firmware_command(oper_type, module_id, crc_val, page_addr)
                ret = self.receive_firmware_command_response()
                retry_count += 1
                if retry_count > max_retry:
                    break
        return ret

    def set_firmware_data(self, module_id, seq_num, bin_data, checksum):
        self.send_firmware_data(module_id, seq_num, bin_data)
        return self.calc_crc64(bin_data, checksum)

    def set_end_flash_data(self, module_id, end_flash_data):
        end_flash_success = False
        page_retry_count = 0
        page_retry_max_count = 10

        while not end_flash_success:
            # Erase page (send erase request and receive erase response)
            erase_page_success = self.set_firmware_command("erase", module_id, 0, 0x0801F800)
            if not erase_page_success:
                self.update_error = -1
                self.update_error_message = "End erase error"
                return False

            # Send data
            checksum = self.set_firmware_data(module_id, 0, end_flash_data, 0)

            # CRC on current page (send CRC request and receive CRC response)
            crc_page_success = self.set_firmware_command("crc", module_id, checksum, 0x0801F800)
            if not crc_page_success:
                if self.update_error == -1:
                    return False
                else:
                    page_retry_count += 1
                    if page_retry_count > page_retry_max_count:
                        self.update_error = -1
                        self.update_error_message = "End crc error"
                        return False
                    continue
            else:
                page_retry_count = 0

            end_flash_success = True
        self.__print(f"End flash is written for network ({module_id})")
        return True

    def update_module_firmware(self):
        self.__print("update_module_firmware")
        self.update_in_progress = True
        self.progress = 0
        self.__print("get network info")
        self.network_uuid, self.network_version = self.get_network_info()

        if self.network_uuid:
            self.network_id = self.network_uuid & 0xFFF
        else:
            self.__print("no response")
            self.update_in_progress = False
            self.update_error = -1
            self.update_error_message = "Network module no response"

        time.sleep(1)

        # set update firmware state
        self.send_set_module_state(0xFFF, Module.UPDATE_FIRMWARE, Module.PNP_OFF)
        self.send_set_module_state(0xFFF, Module.UPDATE_FIRMWARE, Module.PNP_OFF)
        self.send_set_module_state(0xFFF, Module.UPDATE_FIRMWARE, Module.PNP_OFF)

        # wait warning flag
        self.__print("wait warning state")
        timeout = 9
        init_time = time.time()
        is_timeout = False
        wait = 5
        init_wait = time.time()
        while True:
            if time.time() - init_wait > wait:
                break

            recved = self.wait_for_json(timeout)
            if time.time() - init_time > timeout or not recved:
                is_timeout = True
                break

            try:
                json_msg = json.loads(recved)
                if json_msg["c"] == 0x0A:
                    unpacked_data = unpack_data(json_msg["b"], (6, 1))
                    module_uuid = unpacked_data[0]
                    module_id = module_uuid & 0xFFF
                    warning_type = unpacked_data[1]
                    module_type = get_module_type_from_uuid(module_uuid)
                    if module_type != "network":
                        if warning_type == 1:
                            self.send_set_module_state(module_id, Module.UPDATE_FIRMWARE_READY, Module.PNP_OFF)
                        if  warning_type == 2:
                            has_item = False
                            for curr_module_id, curr_module_type in self.modules_to_update:
                                if module_id == curr_module_id:
                                    has_item = True
                                    break

                            for curr_module_id, curr_module_type in self.modules_updated:
                                if module_id == curr_module_id:
                                    has_item = True
                                    return

                            if not has_item:
                                self.__print(f"Adding {module_type} ({module_id}) to waiting list..."f"{' ' * 60}")
                                # Add the module to the waiting list
                                module_elem = module_id, module_type
                                self.modules_to_update.append(module_elem)
            except json.decoder.JSONDecodeError as jde:
                self.__print("json parse error: " + str(jde))

            time.sleep(0.01)

        if is_timeout:
            self.update_in_progress = False
            self.update_error = -1
            self.update_error_message = "Warning timeout"
            return

        if len(self.modules_to_update) == 0:
            self.update_in_progress = False
            self.update_error = -1
            self.update_error_message = "No modules"
            return

        # update network module
        self.__print("update modules")
        self.modules_find = True
        is_success = True
        for curr_module_id, curr_module_type in self.modules_to_update:
            self.module_type = curr_module_type
            self.__print(f"Firmware update {curr_module_type} ({curr_module_id})")
            update_success = self.update_module(curr_module_id, curr_module_type)
            if update_success:
                module_elem = curr_module_id, curr_module_type
                self.modules_updated.append(module_elem)
            else:
                self.__print("update error - " + self.update_error_message)
                is_success = False
                break

        # Reboot all connected modules
        self.send_set_module_state(0xFFF, Module.REBOOT, Module.PNP_OFF)
        self.__print("Reboot message has been sent to all connected modules")

        self.update_in_progress = False

        if self.is_open:
            self.flushInput()
            self.flushOutput()
            self.close()

        if is_success:
            self.update_error = 1

    def update_module(self, module_id, module_type):
        root_path = path.join(path.dirname(__file__), "..", "assets", "firmware", "latest","stm32")
        bin_path = path.join(root_path, f"{module_type.lower()}.bin")
        with open(bin_path, "rb") as bin_file:
            bin_buffer = bin_file.read()

        # Init metadata of the bytes loaded
        page_retry_count = 0
        page_retry_max_count = 20
        page_size = 0x800
        flash_memory_addr = 0x08000000

        bin_size = sys.getsizeof(bin_buffer)
        bin_begin = 0x9000
        bin_end = bin_size - ((bin_size - bin_begin) % page_size)

        page_offset = 0
        for page_begin in range(bin_begin, bin_end + 1, page_size):
            progress = 100 * page_begin // bin_end
            self.progress = progress

            if self.ui:
                num_to_update = len(self.modules_to_update)
                num_updated = len(self.modules_updated)
                if self.ui.is_english:
                    self.ui.update_stm32_modules.setText(f"STM32 modules update is in progress. ({num_updated} / {num_to_update + num_updated})({progress}%)")
                else:
                    self.ui.update_stm32_modules.setText(f"모듈 초기화가 진행중입니다. ({num_updated} / {num_to_update + num_updated})({progress}%)")

            self.__print(f"\rUpdating {module_type} ({module_id}) {self.__progress_bar(page_begin, bin_end)} {progress}%", end="")

            page_end = page_begin + page_size
            curr_page = bin_buffer[page_begin:page_end]

            # Skip current page if empty
            if not sum(curr_page):
                continue
            
            erase_page_success = self.set_firmware_command(
                oper_type = "erase",
                module_id = module_id,
                crc_val = 0,
                page_addr = flash_memory_addr + page_begin + page_offset
            )
            if not erase_page_success:
                self.update_error = -1
                self.update_error_message = "Erase response error"
                return False

            checksum = 0
            for curr_ptr in range(0, page_size, 8):
                if page_begin + curr_ptr >= bin_size:
                    break

                curr_data = curr_page[curr_ptr : curr_ptr + 8]
                checksum = self.set_firmware_data(module_id, curr_ptr // 8, curr_data, checksum)
                self.__delay(0.002)

            # CRC on current page (send CRC request / receive CRC response)
            crc_page_success = self.set_firmware_command(
                oper_type = "crc",
                module_id = module_id,
                crc_val = checksum,
                page_addr = flash_memory_addr + page_begin + page_offset
            )
            if not crc_page_success:
                page_begin -= page_size
                page_retry_count += 1
                if page_retry_count > page_retry_max_count:
                    self.update_error = -1
                    self.update_error_message = "CRC response error"
                    return False
            else:
                page_retry_count = 0

            time.sleep(0.01)

        self.progress = 99
        self.__print(f"\rUpdating {module_type} ({module_id}) {self.__progress_bar(99, 100)} 99%")

        # Get version info from version_path, using appropriate methods
        version_info, version_file = None, "version.txt"
        version_path = root_path + "/" + version_file
        with open(version_path) as version_file:
            version_info = version_file.readline().lstrip("v").rstrip("\n")
        version_digits = [int(digit) for digit in version_info.split(".")]
        """ Version number is formed by concatenating all three version bits
            e.g. 2.2.4 -> 010 00010 00000100 -> 0100 0010 0000 0100
        """
        version = (
            version_digits[0] << 13
            | version_digits[1] << 8
            | version_digits[2]
        )

        # Set end-flash data to be sent at the end of the firmware update
        end_flash_data = bytearray(8)
        end_flash_data[0] = 0xAA
        end_flash_data[6] = version & 0xFF
        end_flash_data[7] = (version >> 8) & 0xFF

        end_flash_success = self.set_end_flash_data(module_id, end_flash_data)
        if not end_flash_success:
            return False
        self.__print(f"Version info (v{version_info}) has been written to its firmware!")

        time.sleep(1)

        self.progress = 100
        self.__print(f"Firmware update is done for {module_type} ({module_id})")
        self.__print("Module firmwares have been updated!")

        return True

    def read_json(self):
        json_pkt = b""
        while json_pkt != b"{":
            if not self.is_open:
                return ""
            json_pkt = self.read()
            if json_pkt == b"":
                return ""
            time.sleep(0.1)
        json_pkt += self.read_until(b"}")
        return json_pkt

    def wait_for_json(self, timeout):
        json_msg = self.read_json()
        init_time = time.time()
        while not json_msg:
            json_msg = self.read_json()
            time.sleep(0.1)
            if time.time() - init_time > timeout:
                return ""
        return json_msg

    @staticmethod
    def __delay(span):
        init_time = time.perf_counter()
        while time.perf_counter() - init_time < span:
            pass
        return

    @staticmethod
    def __compare_version(
        left: str, right: str
    ) -> int:
        left_vars = map(int, left.split('.'))
        right_vars = map(int, right.split('.'))
        for a, b in zip_longest(left_vars, right_vars, fillvalue = 0):
            if a > b:
                return -1
            elif a < b:
                return 1
        return 0

    def calc_crc32(self, data: bytes, crc: int) -> int:
        crc ^= int.from_bytes(data, byteorder="little", signed=False)

        for _ in range(32):
            if crc & (1 << 31) != 0:
                crc = (crc << 1) ^ 0x4C11DB7
            else:
                crc <<= 1
            crc &= 0xFFFFFFFF

        return crc

    def calc_crc64(self, data, checksum):
        checksum = self.calc_crc32(data[:4], checksum)
        checksum = self.calc_crc32(data[4:], checksum)
        return checksum

    def __progress_bar(self, current, total):
        curr_bar = 50 * current // total
        rest_bar = 50 - curr_bar
        return f"[{'=' * curr_bar}>{'.' * rest_bar}]"

    def __print(self, data, end="\n"):
        if self.print:
            print(data, end)

class STM32FirmwareMultiUpdater():
    def __init__(self):
        self.update_in_progress = False
        self.ui = None
        self.list_ui = None

    def set_ui(self, ui, list_ui):
        self.ui = ui
        self.list_ui = list_ui

    def update_module_firmware(self, modi_ports):
        self.stm32_updaters = []
        self.network_uuid = []
        self.state = []
        self.wait_timeout = []
        self.num_to_update = []

        for i, modi_port in enumerate(modi_ports):
            if i > 9:
                break
            try:
                stm32_updater = STM32FirmwareUpdater(modi_port.device)
                stm32_updater.set_print(False)
                stm32_updater.set_raise_error(False)
            except:
                print("open " + modi_port.device + " error")
            else:
                self.stm32_updaters.append(stm32_updater)
                self.state.append(-1)
                self.network_uuid.append('')
                self.wait_timeout.append(0)
                self.num_to_update.append(0)

        if self.list_ui:
            self.list_ui.set_device_num(len(self.stm32_updaters))
            self.list_ui.ui.close_button.setEnabled(False)

        self.update_in_progress = True

        for index, stm32_updater in enumerate(self.stm32_updaters):
            th.Thread(
                target=stm32_updater.update_module_firmware,
                daemon=True
            ).start()
            if self.list_ui:
                self.list_ui.error_message_signal.emit(index, "wait for network uuid")

        delay = 0.1
        while True:
            is_done = True
            total_progress = 0
            for index, stm32_updater in enumerate(self.stm32_updaters):
                if stm32_updater.update_in_progress:
                    if stm32_updater.network_uuid:
                        self.network_uuid[index] = f'0x{stm32_updater.network_uuid:X}'
                        self.list_ui.network_uuid_signal.emit(index, self.network_uuid[index])
                if self.state[index] == -1:
                    # wait module list
                    is_done = False
                    if stm32_updater.modules_find:
                        self.num_to_update[index] = len(stm32_updater.modules_to_update)
                        self.state[index] = 0
                    if stm32_updater.update_error == -1:
                        self.state[index] = 1
                    if self.list_ui:
                        self.list_ui.error_message_signal.emit(index, "wait for module list")
                if self.state[index] == 0:
                    # get module update list (only module update)
                    is_done = False
                    if self.list_ui:
                        self.list_ui.error_message_signal.emit(index, "update modules")
                    if stm32_updater.update_error == 0:
                        current_module_progress = 0
                        total_module_progress = 0

                        if stm32_updater.progress:
                            current_module_progress = stm32_updater.progress
                            if self.num_to_update[index]:
                                total_num = self.num_to_update[index]
                            else:
                                total_num = 1
                            updated = len(stm32_updater.modules_updated) / total_num * 100
                            current = (current_module_progress) / total_num
                            total_module_progress = updated + current
                            total_progress += total_module_progress / len(self.stm32_updaters)

                        if self.list_ui:
                            self.list_ui.current_module_changed_signal.emit(index, stm32_updater.module_type)
                            self.list_ui.progress_signal.emit(index, current_module_progress, total_module_progress)
                    else:
                        self.state[index] = 1

                elif self.state[index] == 1:
                    # end
                    is_done = False
                    if stm32_updater.update_error == 1:
                        total_progress += 100 / len(self.stm32_updaters)
                        if self.list_ui:
                            self.list_ui.network_state_signal.emit(index, 0)
                            self.list_ui.error_message_signal.emit(index, "update success")
                    else:
                        stm32_updater.close()
                        if self.list_ui:
                            self.list_ui.network_state_signal.emit(index, -1)
                            self.list_ui.error_message_signal.emit(index, stm32_updater.update_error_message)

                    if self.list_ui:
                        self.list_ui.progress_signal.emit(index, 100, 100)
                    self.state[index] = 2
                elif self.state[index] == 2:
                    total_progress += 100 / len(self.stm32_updaters)

            if len(self.stm32_updaters):
                print(f"\r{self.__progress_bar(total_progress, 100)}", end="")

                if self.ui:
                    if self.ui.is_english:
                        self.ui.update_stm32_modules.setText(f"STM32 modules update is in progress. ({int(total_progress)}%)")
                    else:
                        self.ui.update_stm32_modules.setText(f"모듈 초기화가 진행중입니다. ({int(total_progress)}%)")

                if self.list_ui:
                    self.list_ui.total_progress_signal.emit(total_progress)
                    self.list_ui.total_status_signal.emit("Uploading...")

            if is_done:
                break

            time.sleep(delay)

        self.update_in_progress = False

        if self.ui:
            self.ui.update_network_stm32.setStyleSheet(
                f"border-image: url({self.ui.active_path}); font-size: 16px"
            )
            self.ui.update_network_stm32.setEnabled(True)
            self.ui.update_network_esp32.setStyleSheet(
                f"border-image: url({self.ui.active_path}); font-size: 16px"
            )
            self.ui.update_network_esp32.setEnabled(True)
            self.ui.update_network_esp32_interpreter.setStyleSheet(
                f"border-image: url({self.ui.active_path}); font-size: 16px"
            )
            self.ui.update_network_esp32_interpreter.setEnabled(True)
            if self.ui.is_english:
                self.ui.update_stm32_modules.setText("Update STM32 Modules.")
            else:
                self.ui.update_stm32_modules.setText("모듈 초기화")

        if self.list_ui:
            self.list_ui.ui.close_button.setEnabled(True)
            self.list_ui.total_status_signal.emit("Complete")
            self.list_ui.total_progress_signal.emit(100)
            for index, stm32_updater in enumerate(self.stm32_updaters):
                self.list_ui.progress_signal.emit(index, 100, 100)

        print("\nSTM firmware update is complete!!")

    @staticmethod
    def __progress_bar(current: int, total: int) -> str:
        curr_bar = int(50 * current // total)
        rest_bar = int(50 - curr_bar)
        return (
            f"Firmware Upload: [{'=' * curr_bar}>{'.' * rest_bar}] "
            f"{100 * current / total:3.1f}%"
        )