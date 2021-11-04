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


class NetworkFirmwareUpdater(serial.Serial):
    """STM32 Network Firmware Updater: Updates a firmware of given module"""

    NO_ERROR = 0
    UPDATE_READY = 1
    WRITE_FAIL = 2
    VERIFY_FAIL = 3
    CRC_ERROR = 4
    CRC_COMPLETE = 5
    ERASE_ERROR = 6
    ERASE_COMPLETE = 7

    NO_RECONNECT = 0
    SOFT_RECONNECT = 1
    HARD_RECONNECT = 2

    REQUEST_RECONNECT_NONE = 0
    REQUEST_DISCONNECT = 1
    REQUEST_RECONNECT = 2
    REQUEST_SOFT_DISCONNECT = 3
    REQUEST_SOFT_RECONNECT = 4

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
        
        self.bootloader = False
        self.network_version = None
        self.network_uuid = None
        self.network_id = None

        self.update_in_progress = False
        self.ui = None

        self.progress = 0

        self.need_to_reconnect = False
        self.reconnect_start_signal = False
        self.reconnect_end_signal = False
        self.popup_reconnect = False
        self.popup_reconnect_mode = self.REQUEST_RECONNECT_NONE
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

    def reconnect_network_module(self, mode, retry_timeout = 5):
        if mode == self.SOFT_RECONNECT:
            self.__print("Temporally disconnecting the serial connection...")
            self.popup_reconnect_mode = self.REQUEST_SOFT_DISCONNECT
            self.flushInput()
            self.flushOutput()
            self.reset_input_buffer()
            self.reset_output_buffer()
            self.close()

            close_time = 5
            time.sleep(close_time)

            self.__print(f"Re-init serial connection for the update, in {int(close_time)} seconds...")
            self.popup_reconnect_mode = self.REQUEST_SOFT_RECONNECT

            is_success = False
            init_time = time.time()
            while True:
                try:
                    for port in stl.comports():
                        if self.location == port.location:
                            self.__print("find port: " + port.name)
                            self.name = port.name
                            break
                    super().__init__(self.name, timeout = 0.1, baudrate = 921600)
                    self.reset_input_buffer()
                    self.reset_output_buffer()
                except serial.SerialException as se:
                    self.__print("error: " + str(se))
                except:
                    self.__print("error")
                else:
                    is_success = True
                    break
                
                if time.time() - init_time > retry_timeout:
                    break
                time.sleep(0.5)

            self.popup_reconnect_mode = self.REQUEST_RECONNECT_NONE
            time.sleep(2)
            if not is_success:
                raise Exception("Reconnect error")
        elif mode == self.HARD_RECONNECT:
            self.flushInput()
            self.flushOutput()
            self.reset_input_buffer()
            self.reset_output_buffer()
            self.close()
            time.sleep(1)

            # popup reconnect message
            self.popup_reconnect = True
            self.popup_reconnect_mode = self.REQUEST_DISCONNECT

            # wait disconnect
            self.__print("disconnect " + self.name)
            while True:
                is_disconnected = True
                for port in stl.comports():
                    if self.location == port.location:
                        is_disconnected = False
                if is_disconnected:
                    break
                time.sleep(0.1)

            time.sleep(0.2)
            self.popup_reconnect_mode = self.REQUEST_RECONNECT

            self.__print("connect " + self.name)
            # wait connect
            while True:
                is_connected = False
                for port in stl.comports():
                    if self.location == port.location:
                        self.name = port.name
                        is_connected = True
                        break
                if is_connected:
                    break
                time.sleep(0.1)

            time.sleep(1)

            self.popup_reconnect = False
            self.popup_reconnect_mode = self.REQUEST_RECONNECT_NONE

            # reconnect
            self.__print("try reconnect")
            super().__init__(self.name, timeout = 0.1, baudrate = 921600)
            self.reset_input_buffer()
            self.reset_output_buffer()
            time.sleep(1)

    def update_module_firmware(self, bootloader):
        self.__print("update_module_firmware")
        self.bootloader = bootloader
        self.update_in_progress = True
        self.progress = 0

        if self.bootloader:
            self.__print("get network info")
            self.network_uuid, self.network_version = self.get_network_info()

            if self.network_uuid:
                self.network_id = self.network_uuid & 0xFFF
            else:
                self.network_id = 0xFFF

            self.__print("update network module")
            self.progress = 50
            self.send_set_network_module_state(self.network_id, Module.UPDATE_FIRMWARE, Module.PNP_OFF)
            time.sleep(5)
            self.progress = 100

            if self.is_open:
                self.flushInput()
                self.flushOutput()
                self.close()
            time.sleep(5)
            self.update_error = 1
            self.update_in_progress = False
        else:
            # wait warning flag
            self.__print("wait warning state")
            timeout = 10
            init_time = time.time()
            is_timeout = False
            retry = 0
            max_retry = 5
            while True:
                recved = self.wait_for_json(timeout)
                if not recved:
                    retry += 1
                    if retry > max_retry:
                        is_timeout = True
                        break
                    continue

                if time.time() - init_time > timeout or not recved:
                    is_timeout = True
                    break

                try:
                    json_msg = json.loads(recved)
                    if json_msg["c"] == 0x0A:
                        unpacked_data = unpack_data(json_msg["b"], (6, 1))
                        module_uuid = unpacked_data[0]
                        warning_type = unpacked_data[1]
                        module_type = get_module_type_from_uuid(module_uuid)
                        if module_type == "network":
                            if not self.network_uuid:
                                self.network_uuid = module_uuid
                                self.network_id = self.network_uuid & 0xFFF

                            if warning_type == 1:
                                self.send_set_module_state(self.network_id, Module.UPDATE_FIRMWARE_READY, Module.PNP_OFF)
                            if  warning_type == 2:
                                break
                except json.decoder.JSONDecodeError as jde:
                    self.__print("json parse error: " + str(jde))

                time.sleep(0.01)

            if is_timeout:
                self.update_in_progress = False
                self.update_error = -1
                self.update_error_message = "Warning timeout"
                if self.is_open:
                    self.flushInput()
                    self.flushOutput()
                    self.close()
                return

            # update network module
            self.__print("update network module")
            update_success = self.update_network_module(self.network_id)
            if not update_success:
                self.__print("update error - " + self.update_error_message)

            if self.is_open:
                self.flushInput()
                self.flushOutput()
                self.close()

            self.update_in_progress = False
            self.update_error = 1

    def update_network_module(self, module_id):
        root_path = path.join(path.dirname(__file__), "..", "assets", "firmware", "latest","stm32")
        bin_path = path.join(root_path, "network.bin")
        with open(bin_path, "rb") as bin_file:
            bin_buffer = bin_file.read()

        # Init metadata of the bytes loaded
        page_retry_count = 0
        page_retry_max_count = 20
        page_size = 0x800
        flash_memory_addr = 0x08000000

        bin_size = sys.getsizeof(bin_buffer)
        bin_begin = page_size
        bin_end = bin_size - ((bin_size - bin_begin) % page_size)

        page_offset = 0x8800
        page_begin = bin_begin
        while page_begin < bin_end :
        # for page_begin in range(bin_begin, bin_end + 1, page_size):
            progress = 100 * page_begin // bin_end
            self.progress = progress

            if self.ui:
                if self.bootloader:
                    if self.ui.is_english:
                        self.ui.update_network_stm32_bootloader.setText(f"Network bootloader is in progress. ({progress}%)")
                    else:
                        self.ui.update_network_stm32_bootloader.setText(f"네트워크 모듈 부트로터 진행중입니다. ({progress}%)")
                else:
                    if self.ui.is_english:
                        self.ui.update_network_stm32.setText(f"Network STM32 update is in progress. ({progress}%)")
                    else:
                        self.ui.update_network_stm32.setText(f"네트워크 모듈 초기화가 진행중입니다. ({progress}%)")

            self.__print(f"\rUpdating network ({module_id}) {self.__progress_bar(page_begin, bin_end)} {progress}%", end="")

            page_end = page_begin + page_size
            curr_page = bin_buffer[page_begin:page_end]

            # Skip current page if empty
            if not sum(curr_page):
                page_begin = page_begin + page_size
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
                self.__delay(0.001)

            # CRC on current page (send CRC request / receive CRC response)
            crc_page_success = self.set_firmware_command(
                oper_type = "crc",
                module_id = module_id,
                crc_val = checksum,
                page_addr = flash_memory_addr + page_begin + page_offset
            )
            if not crc_page_success:
                # page_begin -= page_size
                page_retry_count += 1
                if page_retry_count > page_retry_max_count:
                    self.update_error = -1
                    self.update_error_message = "CRC response error"
                    return False
                page_begin = page_begin + page_size
            else:
                page_retry_count = 0
            page_begin = page_begin + page_size
            time.sleep(0.01)

        self.progress = 99
        self.__print(f"\rUpdating network ({module_id}) {self.__progress_bar(99, 100)} 99%")

        # Get version info from version_path, using appropriate methods
        version_info, version_file = None, "base_version.txt"
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
        self.__print(f"Firmware update is done for network ({module_id})")

        # Reboot all connected modules
        self.send_set_module_state(0xFFF, Module.REBOOT, Module.PNP_OFF)
        self.__print("Reboot message has been sent to all connected modules")

        time.sleep(1)

        self.progress = 100
        self.__print(f"\rUpdating network ({module_id}) {self.__progress_bar(100, 100)} 100%")
        self.__print("Module firmwares have been updated!")

        time.sleep(1)
        
        if self.is_open:
            self.flushInput()
            self.flushOutput()
            self.close()

        if self.ui:
            self.ui.update_stm32_modules.setStyleSheet(f"border-image: url({self.ui.active_path}); font-size: 16px")
            self.ui.update_stm32_modules.setEnabled(True)
            self.ui.update_network_esp32.setStyleSheet(f"border-image: url({self.ui.active_path}); font-size: 16px")
            self.ui.update_network_esp32.setEnabled(True)
            self.ui.update_network_esp32_interpreter.setStyleSheet(f"border-image: url({self.ui.active_path}); font-size: 16px")
            self.ui.update_network_esp32_interpreter.setEnabled(True)
            if self.bootloader:
                self.ui.update_network_stm32.setStyleSheet(f"border-image: url({self.ui.active_path}); font-size: 16px")
                self.ui.update_network_stm32.setEnabled(True)
                if self.ui.is_english:
                    self.ui.update_network_stm32_bootloader.setText("Set Network Bootloader STM32")
                else:
                    self.ui.update_network_stm32_bootloader.setText("네트워크 모듈 부트로더")
            else:
                self.ui.update_network_stm32_bootloader.setStyleSheet(f"border-image: url({self.ui.active_path}); font-size: 16px")
                self.ui.update_network_stm32_bootloader.setEnabled(True)
                if self.ui.is_english:
                    self.ui.update_network_stm32.setText("Update Network STM32")
                else:
                    self.ui.update_network_stm32.setText("네트워크 모듈 초기화")

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
        time.sleep(span)
        # init_time = time.perf_counter()
        # while time.perf_counter() - init_time < span:
        #     pass
        # return

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
            print(self.name, end = " - ")
            print(data, end)

class NetworkFirmwareMultiUpdater():
    def __init__(self):
        self.update_in_progress = False
        self.ui = None
        self.list_ui = None

    def set_ui(self, ui, list_ui):
        self.ui = ui
        self.list_ui = list_ui

    def update_module_firmware(self, modi_ports, bootloader):
        self.network_updaters = []
        self.network_uuid = []
        self.state = []
        self.wait_timeout = []
        self.num_to_update = []

        for i, modi_port in enumerate(modi_ports):
            if i > 9:
                break
            try:
                network_updater = NetworkFirmwareUpdater(modi_port.device)
                network_updater.set_print(False)
                network_updater.set_raise_error(False)
            except:
                print("open " + modi_port.device + " error")
            else:
                self.network_updaters.append(network_updater)
                self.state.append(0)
                self.network_uuid.append('')
                self.wait_timeout.append(0)
                self.num_to_update.append(0)

        if self.list_ui:
            self.list_ui.set_device_num(len(self.network_updaters))
            self.list_ui.ui.close_button.setEnabled(False)

        self.update_in_progress = True

        for index, network_updater in enumerate(self.network_updaters):
            th.Thread(
                target=network_updater.update_module_firmware,
                args=(bootloader, ),
                daemon=True
            ).start()
            if self.list_ui:
                self.list_ui.error_message_signal.emit(index, "Wait for network uuid")

        delay = 0.1
        reconnect_device = []
        while True:
            is_done = True
            total_progress = 0
            for index, network_updater in enumerate(self.network_updaters):
                if network_updater.network_uuid:
                    self.network_uuid[index] = f'0x{network_updater.network_uuid:X}'
                    self.list_ui.network_uuid_signal.emit(index, self.network_uuid[index])
                if self.list_ui:
                    if network_updater.popup_reconnect_mode == 1:
                        self.list_ui.error_message_signal.emit(index, "Please disconnect")
                        self.list_ui.network_state_signal.emit(index, network_updater.popup_reconnect_mode)
                    elif network_updater.popup_reconnect_mode == 2:
                        self.list_ui.error_message_signal.emit(index, "Please reconnect")
                        self.list_ui.network_state_signal.emit(index, network_updater.popup_reconnect_mode)
                    elif network_updater.popup_reconnect_mode == 3:
                        self.list_ui.error_message_signal.emit(index, "Disconnecting.....")
                        self.list_ui.network_state_signal.emit(index, 0)
                    elif network_updater.popup_reconnect_mode == 4:
                        self.list_ui.error_message_signal.emit(index, "Reconnecting.....")
                        self.list_ui.network_state_signal.emit(index, 0)

                if network_updater.need_to_reconnect:
                    if not index in reconnect_device:
                        reconnect_device.append(index)
                if len(reconnect_device):
                    if index == reconnect_device[0]:
                        network_updater.reconnect_start_signal = True
                        if network_updater.reconnect_end_signal:
                            reconnect_device.pop(0)
                        else:
                            if network_updater.update_error != 0:
                                reconnect_device.pop(0)

                if self.state[index] == 0:
                    # update modules
                    is_done = is_done & False
                    if network_updater.update_error == 0:
                        current_module_progress = network_updater.progress
                        total_module_progress = network_updater.progress
                        total_progress += total_module_progress / len(self.network_updaters)

                        if self.list_ui:
                            self.list_ui.current_module_changed_signal.emit(index, "network")
                            self.list_ui.progress_signal.emit(index, current_module_progress, total_module_progress)
                    else:
                        total_progress += 100 / len(self.network_updaters)
                        self.state[index] = 1
                    if self.list_ui and network_updater.popup_reconnect_mode == 0:
                        self.list_ui.error_message_signal.emit(index, "Updating module")
                        self.list_ui.network_state_signal.emit(index, 0)
                elif self.state[index] == 1:
                    # end
                    total_progress += 100 / len(self.network_updaters)
                    if network_updater.update_error == 1:
                        # update success
                        print("update success: " + self.network_uuid[index])
                        if self.list_ui:
                            self.list_ui.network_state_signal.emit(index, 0)
                            self.list_ui.error_message_signal.emit(index, "Update success")
                    else:
                        # update error
                        print("update error: " + self.network_uuid[index] + " - " + network_updater.update_error_message)
                        if self.list_ui:
                            self.list_ui.network_state_signal.emit(index, -1)
                            self.list_ui.error_message_signal.emit(index, network_updater.update_error_message)

                    if self.list_ui:
                        self.list_ui.progress_signal.emit(index, 100, 100)
                    self.state[index] = 2
                elif self.state[index] == 2:
                    total_progress += 100 / len(self.network_updaters)


            if len(self.network_updaters):
                print(f"\r{self.__progress_bar(total_progress, 100)}", end="")

                if self.ui:
                    if bootloader:
                        if self.ui.is_english:
                            self.ui.update_network_stm32_bootloader.setText(f"Network bootloader is in progress. ({int(total_progress)}%)")
                        else:
                            self.ui.update_network_stm32_bootloader.setText(f"네트워크 모듈 부트로터 진행중입니다. ({int(total_progress)}%)")
                    else:
                        if self.ui.is_english:
                            self.ui.update_network_stm32.setText(f"Network STM32 update is in progress. ({int(total_progress)}%)")
                        else:
                            self.ui.update_network_stm32.setText(f"네트워크 모듈 초기화가 진행중입니다. ({int(total_progress)}%)")

                if self.list_ui:
                    self.list_ui.total_progress_signal.emit(total_progress)
                    self.list_ui.total_status_signal.emit("Uploading...")

            if is_done:
                break

            time.sleep(delay)

        self.update_in_progress = False

        if self.ui:
            self.ui.update_stm32_modules.setStyleSheet(f"border-image: url({self.ui.active_path}); font-size: 16px")
            self.ui.update_stm32_modules.setEnabled(True)
            self.ui.update_network_esp32.setStyleSheet(f"border-image: url({self.ui.active_path}); font-size: 16px")
            self.ui.update_network_esp32.setEnabled(True)
            self.ui.update_network_esp32_interpreter.setStyleSheet(f"border-image: url({self.ui.active_path}); font-size: 16px")
            self.ui.update_network_esp32_interpreter.setEnabled(True)
            if bootloader:
                self.ui.update_network_stm32.setStyleSheet(f"border-image: url({self.ui.active_path}); font-size: 16px")
                self.ui.update_network_stm32.setEnabled(True)
                if self.ui.is_english:
                    self.ui.update_network_stm32_bootloader.setText("Set Network Bootloader STM32")
                else:
                    self.ui.update_network_stm32_bootloader.setText("네트워크 모듈 부트로더")
            else:
                self.ui.update_network_stm32_bootloader.setStyleSheet(f"border-image: url({self.ui.active_path}); font-size: 16px")
                self.ui.update_network_stm32_bootloader.setEnabled(True)
                if self.ui.is_english:
                    self.ui.update_network_stm32.setText("Update Network STM32")
                else:
                    self.ui.update_network_stm32.setText("네트워크 모듈 초기화")

        if self.list_ui:
            self.list_ui.ui.close_button.setEnabled(True)
            self.list_ui.total_status_signal.emit("Complete")
            self.list_ui.total_progress_signal.emit(100)
            for index, network_updater in enumerate(self.network_updaters):
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