
import io
import sys
import time
import json
import serial
import zipfile

import threading as th
import urllib.request as ur

from os import path
from io import open
from base64 import b64encode
from urllib.error import URLError

from modi_firmware_updater.util.connection_util import SerTask, list_modi_ports 
from modi_firmware_updater.util.message_util import (
    unpack_data, decode_message, parse_message
)
from modi_firmware_updater.util.module_util import (
    Module, get_module_type_from_uuid
)


def retry(exception_to_catch):
    def decorator(func):
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except exception_to_catch:
                return wrapper(*args, **kwargs)

        return wrapper

    return decorator


class STM32FirmwareUpdater:
    """STM32 Firmware Updater: Updates a firmware of given module"""

    NO_ERROR = 0
    UPDATE_READY = 1
    WRITE_FAIL = 2
    VERIFY_FAIL = 3
    CRC_ERROR = 4
    CRC_COMPLETE = 5
    ERASE_ERROR = 6
    ERASE_COMPLETE = 7

    def __init__(
        self, is_os_update=True, target_ids=(0xFFF, ), conn_type='ser'
    ):
        self.conn_type = conn_type
        self.update_network_base = False
        self.__conn = self.__open_conn()
        self.__conn.open_conn()
        th.Thread(target=self.__read_conn, daemon=True).start()
        self.__target_ids = target_ids
        self.response_flag = False
        self.response_error_flag = False
        self.response_error_count = 0
        self.__running = True
        self.__is_os_update = is_os_update
        self.update_event = th.Event()
        self.update_in_progress = False
        self.modules_to_update = []
        self.modules_updated = []
        self.network_id = None
        self.ui = None

        self.request_network_id()

    def __del__(self):
        try:
            self.close()
        except:
            print('Magic del is called with an exception')

    def set_ui(self, ui):
        self.ui = ui

    def request_network_id(self):
        self.__conn.send_nowait(
            parse_message(0x28, 0xFFF, 0xFFF, (0xFF, 0x0F))
        )

    def __assign_network_id(self, sid, data):
        module_uuid = unpack_data(data, (6, 1))[0]
        module_type = get_module_type_from_uuid(module_uuid)
        if module_type == 'network':
            self.network_id = sid

    def update_module_firmware(self, update_network_base=False):
        if update_network_base:
            r_mode = 1
            self.update_network_base = True
            # Retrieve the network id only and update it accordingly
            timeout, delay = 3, 0.2
            while not self.network_id:
                if timeout <= 0:
                    if not self.update_in_progress:
                        print(
                            'Could not retrieve network id, '
                            'broadcast id will be used instead.'
                        )
                    self.network_id = 0xFFF
                    r_mode = 2
                    break
                self.request_network_id()
                timeout -= delay
                time.sleep(delay)
            """
            If network id could not be retrieved, it's probably the case that
            the network is already in the update progress. As such, we skip to
            request to update the base firmware.
            """
            if self.network_id != 0xFFF:
                print(
                    f'Sending a request to update firmware of network '
                    f'({self.network_id})'
                )
            if not self.update_in_progress:
                self.request_to_update_firmware(
                    self.network_id, is_network=True, reinit_mode=r_mode
                )
        else:
            self.reset_state()
            for target in self.__target_ids:
                self.request_to_update_firmware(target)
        self.update_event.wait()
        print("Module firmwares have been updated!")
        self.close()

    def close(self):
        self.__running = False
        time.sleep(0.5)
        self.__conn.close_conn()

    def __open_conn(self):
        return SerTask()

    def _reconnect_serial_connection(self, modi_num):
        while True:
            time.sleep(0.1)
            disconnect = False
            if not list_modi_ports():
                disconnect = True
            if disconnect:
                if modi_num == len(list_modi_ports()):
                    self.__reinitialize_serial_connection()
                    break

    def reinitialize_serial_connection(self, reinit_mode=1):
        if self.ui and self.update_network_base and reinit_mode == 2:
            modi_num = len(list_modi_ports())
            if self.ui.is_english:
                self.ui.update_network_stm32.setText(
                    "Reconnect network module and click the button again please."
                )
            else:
                self.ui.update_network_stm32.setText(
                    "네트워크 모듈을 재연결 후 버튼을 다시 눌러주십시오."
                )
            th.Thread(
                target=self._reconnect_serial_connection,
                args=(modi_num,),
                daemon=True
            ).start()
        else:
            self.__reinitialize_serial_connection()

    def __reinitialize_serial_connection(self):
        print('Temporally disconnecting the serial connection...')
        self.close()
        time.sleep(2)
        print('Re-init serial connection for the update, in 2 seconds...')
        self.__conn = self.__open_conn()
        self.__conn.open_conn()
        self.__running = True
        th.Thread(target=self.__read_conn, daemon=True).start()

    def reset_state(self, update_in_progress: bool = False) -> None:
        self.response_flag = False
        self.response_error_flag = False
        self.response_error_count = 0
        self.update_in_progress = False

        if not update_in_progress:
            print('Make sure you have connected module(s) to update')
            print("Resetting firmware updater's state")
            self.modules_to_update = []
            self.modules_updated = []

    def request_to_update_firmware(
        self, module_id, is_network=False, reinit_mode=1
    ) -> None:
        # Remove firmware of MODI modules (Removes EndFlash)
        if is_network:
            firmware_update_message = self.__set_network_state(
                module_id, 4, Module.PNP_OFF
            )
            self.__conn.send_nowait(firmware_update_message)
            self.reinitialize_serial_connection(reinit_mode)
        else:
            firmware_update_message = self.__set_module_state(
                module_id, Module.UPDATE_FIRMWARE, Module.PNP_OFF
            )
            self.__conn.send_nowait(firmware_update_message)
        print('Firmware update has been requested')

    def check_to_update_firmware(self, module_id: int) -> None:
        firmware_update_ready_message = self.__set_module_state(
            module_id, Module.UPDATE_FIRMWARE_READY, Module.PNP_OFF
        )
        self.__conn.send_nowait(firmware_update_ready_message)

    def add_to_waitlist(self, module_id: int, module_type: str) -> None:
        # Check if input module already exist in the list
        for curr_module_id, curr_module_type in self.modules_to_update:
            if module_id == curr_module_id:
                return

        # Check if module is already updated
        for curr_module_id, curr_module_type in self.modules_updated:
            if module_id == curr_module_id:
                return

        print(f"Adding {module_type} ({module_id}) to waiting list..."
              f"{' ' * 60}")

        # Add the module to the waiting list
        module_elem = module_id, module_type
        self.modules_to_update.append(module_elem)

    def update_module(self, module_id: int, module_type: str) -> None:
        if self.update_in_progress:
            return

        self.update_in_progress = True
        updater_thread = th.Thread(
            target=self.__update_firmware, args=(module_id, module_type)
        )
        updater_thread.daemon = True
        updater_thread.start()

    def update_response(self, response: bool,
                        is_error_response: bool = False) -> None:
        if not is_error_response:
            self.response_flag = response
        else:
            self.response_error_flag = response

    def __update_firmware(self, module_id: int, module_type: str) -> None:
        self.update_in_progress = True
        self.modules_updated.append((module_id, module_type))

        # Init base root_path, utilizing local binary files
        root_path = (
            path.join(
                path.dirname(__file__),
                '..', 'assets', 'firmware', 'stm32'
            )
        )

        if self.__is_os_update:
            if self.ui:
                if self.update_network_base:
                    root_path = (
                        'https://download.luxrobo.com/modi-network-os'
                    )
                    zip_path = root_path + '/network.zip'
                    bin_path = 'network.bin'
                else:
                    root_path = (
                        'https://download.luxrobo.com/modi-skeleton-mobile'
                    )
                    zip_path = root_path + '/skeleton.zip'
                    bin_path = (
                        path.join(f'skeleton/{module_type.lower()}.bin')
                        if module_type != 'env' else
                        path.join('skeleton/environment.bin')
                    )

                try:
                    with ur.urlopen(zip_path, timeout=5) as conn:
                        download_response = conn.read()
                except URLError:
                    raise URLError(
                        "Failed to download firmware. Check your internet."
                    )
                zip_content = zipfile.ZipFile(
                    io.BytesIO(download_response), 'r'
                )
                bin_buffer = zip_content.read(bin_path)
            else:
                bin_path = path.join(root_path, f"{module_type.lower()}.bin")
                with open(bin_path, 'rb') as bin_file:
                    bin_buffer = bin_file.read()

            # Init metadata of the bytes loaded
            page_size = 0x800
            flash_memory_addr = 0x08000000

            bin_size = sys.getsizeof(bin_buffer)
            bin_begin = 0x9000 if not self.update_network_base else page_size
            bin_end = bin_size - ((bin_size - bin_begin) % page_size)

            page_offset = 0 if not self.update_network_base else 0x8800
            for page_begin in range(bin_begin, bin_end + 1, page_size):
                progress = 100 * page_begin // bin_end

                if self.ui:
                    if self.update_network_base:
                        if self.ui.is_english:
                            self.ui.update_network_stm32.setText(
                                f"Network STM32 update is in progress. "
                                f"({progress}%)"
                            )
                        else:
                            self.ui.update_network_stm32.setText(
                                f"네트워크 모듈 초기화가 진행중입니다. "
                                f"({progress}%)"
                            )
                    else:
                        if self.ui.is_english:
                            self.ui.update_stm32_modules.setText(
                                f"STM32 modules update is in progress. "
                                f"({progress}%)"
                            )
                        else:
                            self.ui.update_stm32_modules.setText(
                                f"모듈 초기화가 진행중입니다. ({progress}%)"
                            )

                print(
                    f"\rUpdating {module_type} ({module_id}) "
                    f"{self.__progress_bar(page_begin, bin_end)} "
                    f"{progress}%", end=''
                )

                page_end = page_begin + page_size
                curr_page = bin_buffer[page_begin:page_end]

                # Skip current page if empty
                if not sum(curr_page):
                    continue

                # Erase page (send erase request and receive its response)
                erase_page_success = self.send_firmware_command(
                    oper_type="erase", module_id=module_id, crc_val=0,
                    dest_addr=flash_memory_addr,
                    page_addr=page_begin + page_offset
                )
                if not erase_page_success:
                    page_begin -= page_size
                    continue
                # Copy current page data to the module's memory
                checksum = 0
                for curr_ptr in range(0, page_size, 8):
                    if page_begin + curr_ptr >= bin_size:
                        break

                    curr_data = curr_page[curr_ptr:curr_ptr + 8]
                    checksum = self.send_firmware_data(
                        module_id,
                        seq_num=curr_ptr // 8,
                        bin_data=curr_data,
                        crc_val=checksum
                    )
                    self.__delay(0.002)

                # CRC on current page (send CRC request / receive CRC response)
                crc_page_success = self.send_firmware_command(
                    oper_type="crc", module_id=module_id, crc_val=checksum,
                    dest_addr=flash_memory_addr,
                    page_addr=page_begin + page_offset
                )
                if not crc_page_success:
                    page_begin -= page_size
                time.sleep(0.01)
        print(
            f"\rUpdating {module_type} ({module_id}) "
            f"{self.__progress_bar(1, 1)} 100%"
        )

        # Get version info from version_path, using appropriate methods
        version_info, version_file = None, 'version.txt'
        if self.ui:
            version_path = root_path + '/' + version_file
            for line in ur.urlopen(version_path, timeout=5):
                version_info = line.decode('utf-8').lstrip('v')
        else:
            if self.update_network_base:
                version_file = 'base_' + version_file
            version_path = root_path + '/' + version_file
            with open(version_path) as version_file:
                version_info = version_file.readline().lstrip('v').rstrip('\n')
        version_digits = [int(digit) for digit in version_info.split('.')]
        """ Version number is formed by concatenating all three version bits
            e.g. 2.2.4 -> 010 00010 00000100 -> 0100 0010 0000 0100
        """
        version = (
            version_digits[0] << 13 |
            version_digits[1] << 8 |
            version_digits[2]
        )

        # Set end-flash data to be sent at the end of the firmware update
        end_flash_data = bytearray(8)
        end_flash_data[0] = 0xAA
        end_flash_data[6] = version & 0xFF
        end_flash_data[7] = (version >> 8) & 0xFF
        self.send_end_flash_data(module_type, module_id, end_flash_data)
        print(
            f'Version info (v{version_info}) has been written to its firmware!'
        )

        # Firmware update flag down, resetting used flags
        print(f'Firmware update is done for {module_type} ({module_id})')
        self.reset_state(update_in_progress=True)

        if self.modules_to_update:
            print("Processing the next module to update the firmware..")
            next_module_id, next_module_type = self.modules_to_update.pop(0)
            self.__update_firmware(next_module_id, next_module_type)
        else:
            # Reboot all connected modules
            reboot_message = self.__set_module_state(
                0xFFF, Module.REBOOT, Module.PNP_OFF
            )
            self.__conn.send_nowait(reboot_message)
            print("Reboot message has been sent to all connected modules")
            self.reset_state()
            if self.update_network_base:
                self.reinitialize_serial_connection(reinit_mode=1)
                time.sleep(0.5)

            time.sleep(1)
            self.update_in_progress = False
            self.update_event.set()

            if self.ui:
                if self.update_network_base:
                    self.ui.update_stm32_modules.setStyleSheet(
                        f'border-image: url({self.ui.active_path});'
                        'font-size: 16px'
                    )
                    self.ui.update_stm32_modules.setEnabled(True)
                    if self.ui.is_english:
                        self.ui.update_network_stm32.setText(
                            "Update Network STM32"
                        )
                    else:
                        self.ui.update_network_stm32.setText(
                            "네트워크 모듈 초기화"
                        )
                else:
                    self.ui.update_network_stm32.setStyleSheet(
                        f'border-image: url({self.ui.active_path});'
                        'font-size: 16px'
                    )
                    self.ui.update_network_stm32.setEnabled(True)
                    if self.ui.is_english:
                        self.ui.update_stm32_modules.setText(
                            "Update STM32 Modules."
                        )
                    else:
                        self.ui.update_stm32_modules.setText(
                            "모듈 초기화"
                        )
                self.ui.update_network_esp32.setStyleSheet(
                    f'border-image: url({self.ui.active_path}); font-size: 16px'
                )
                self.ui.update_network_esp32.setEnabled(True)

    @staticmethod
    def __delay(span):
        init_time = time.perf_counter()
        while time.perf_counter() - init_time < span:
            pass
        return

    @staticmethod
    def __set_network_state(destination_id: int, module_state: int,
                            pnp_state: int) -> str:
        message = dict()

        message["c"] = 0xA4
        message["s"] = 0
        message["d"] = destination_id

        state_bytes = bytearray(2)
        state_bytes[0] = module_state
        state_bytes[1] = pnp_state

        message["b"] = b64encode(bytes(state_bytes)).decode("utf-8")
        message["l"] = 2

        return json.dumps(message, separators=(",", ":"))

    @staticmethod
    def __set_module_state(destination_id: int, module_state: int,
                           pnp_state: int) -> str:
        message = dict()

        message["c"] = 0x09
        message["s"] = 0
        message["d"] = destination_id

        state_bytes = bytearray(2)
        state_bytes[0] = module_state
        state_bytes[1] = pnp_state

        message["b"] = b64encode(bytes(state_bytes)).decode("utf-8")
        message["l"] = 2

        return json.dumps(message, separators=(",", ":"))

    # TODO: Use retry decorator here
    @retry(Exception)
    def send_end_flash_data(self, module_type: str, module_id: int,
                            end_flash_data: bytearray) -> None:
        # Write end-flash data until success
        end_flash_success = False
        while not end_flash_success:

            # Erase page (send erase request and receive erase response)
            erase_page_success = self.send_firmware_command(
                oper_type="erase", module_id=module_id, crc_val=0,
                dest_addr=0x0801F800
            )
            # TODO: Remove magic number of dest_addr above, try using flash_mem
            if not erase_page_success:
                continue

            # Send data
            checksum = self.send_firmware_data(
                module_id, seq_num=0, bin_data=end_flash_data, crc_val=0
            )

            # CRC on current page (send CRC request and receive CRC response)
            crc_page_success = self.send_firmware_command(
                oper_type="crc", module_id=module_id, crc_val=checksum,
                dest_addr=0x0801F800
            )
            if not crc_page_success:
                continue

            end_flash_success = True
        # print(f"End flash is written for {module_type} ({module_id})")

    def get_firmware_command(self, module_id: int, rot_stype: int,
                             rot_scmd: int, crc32: int, page_addr: int) -> str:
        message = dict()
        message["c"] = 0x0D

        """ SID is 12-bits length in MODI CAN.
            To fully utilize its capacity, we split 12-bits into 4 and 8 bits.
            First 4 bits include rot_scmd information.
            And the remaining bits represent rot_stype.
        """
        message["s"] = (rot_scmd << 8) | rot_stype
        message["d"] = module_id

        """ The firmware command data to be sent is 8-bytes length.
            Where the first 4 bytes consist of CRC-32 information.
            Last 4 bytes represent page address information.
        """
        crc32_and_page_addr_data = bytearray(8)
        for i in range(4):
            crc32_and_page_addr_data[i] = crc32 & 0xFF
            crc32 >>= 8
            crc32_and_page_addr_data[4 + i] = page_addr & 0xFF
            page_addr >>= 8
        message["b"] = b64encode(
            bytes(crc32_and_page_addr_data)
        ).decode("utf-8")
        message["l"] = 8

        return json.dumps(message, separators=(",", ":"))

    def get_firmware_data(self, module_id: int, seq_num: int,
                          bin_data: bytes) -> str:
        message = dict()
        message["c"] = 0x0B
        message["s"] = seq_num
        message["d"] = module_id

        message["b"] = b64encode(bytes(bin_data)).decode("utf-8")
        message["l"] = 8

        return json.dumps(message, separators=(",", ":"))

    def calc_crc32(self, data: bytes, crc: int) -> int:
        crc ^= int.from_bytes(data, byteorder='little', signed=False)

        for _ in range(32):
            if crc & (1 << 31) != 0:
                crc = (crc << 1) ^ 0x4C11DB7
            else:
                crc <<= 1
            crc &= 0xFFFFFFFF

        return crc

    def calc_crc64(self, data: bytes, checksum: int) -> int:
        checksum = self.calc_crc32(data[:4], checksum)
        checksum = self.calc_crc32(data[4:], checksum)
        return checksum

    def send_firmware_command(self, oper_type: str, module_id: int,
                              crc_val: int, dest_addr: int,
                              page_addr: int = 0) -> bool:
        rot_scmd = 2 if oper_type == "erase" else 1

        # Send firmware command request
        request_message = self.get_firmware_command(
            module_id, 1, rot_scmd, crc_val, page_addr=dest_addr + page_addr
        )
        self.__conn.send_nowait(request_message)

        return self.receive_command_response()

    def receive_command_response(self, response_delay: float = 0.001,
                                 response_timeout: float = 5,
                                 max_response_error_count: int = 75) -> bool:
        # Receive firmware command response
        response_wait_time = 0
        while not self.response_flag:
            # Calculate timeout at each iteration
            time.sleep(response_delay)
            response_wait_time += response_delay

            # If timed-out
            if response_wait_time > response_timeout:
                raise Exception("Response timed-out")

            # If error is raised
            if self.response_error_flag:
                self.response_error_count += 1
                if self.response_error_count > max_response_error_count:
                    raise Exception("Response Errored")
                self.response_error_flag = False
                return False

        self.response_flag = False
        return True

    def send_firmware_data(self, module_id: int, seq_num: int, bin_data: bytes,
                           crc_val: int) -> int:
        # Send firmware data
        data_message = self.get_firmware_data(
            module_id, seq_num=seq_num, bin_data=bin_data
        )
        self.__conn.send_nowait(data_message)

        # Calculate crc32 checksum twice
        checksum = self.calc_crc64(data=bin_data, checksum=crc_val)
        return checksum

    def __progress_bar(self, current: int, total: int) -> str:
        curr_bar = 50 * current // total
        rest_bar = 50 - curr_bar
        return f"[{'=' * curr_bar}>{'.' * rest_bar}]"

    def __read_conn(self):
        while True:
            self.__handle_message()
            time.sleep(0.001)
            if not self.__running:
                break

    def __handle_message(self):
        msg = self.__conn.recv()
        if not msg:
            return

        try:
            ins, sid, did, data, length = decode_message(msg)
        except json.JSONDecodeError:
            return
        command = {
            0x05: self.__assign_network_id,
            0x0A: self.__update_warning,
            0x0C: self.__update_firmware_state
        }.get(ins)

        if command:
            command(sid, data)

    def __update_firmware_state(self, sid: int, data: str):
        message_decoded = unpack_data(data, (4, 1))
        stream_state = message_decoded[1]

        if stream_state == self.CRC_ERROR:
            self.update_response(response=True, is_error_response=True)
        elif stream_state == self.CRC_COMPLETE:
            self.update_response(response=True)
        elif stream_state == self.ERASE_ERROR:
            self.update_response(response=True, is_error_response=True)
        elif stream_state == self.ERASE_COMPLETE:
            self.update_response(response=True)

    def __update_warning(self, sid: int, data: str) -> None:
        module_uuid = unpack_data(data, (6, 1))[0]
        warning_type = unpack_data(data, (6, 1))[1]

        # If warning shows current module works fine, return immediately
        if not warning_type:
            return

        module_id = sid
        module_type = get_module_type_from_uuid(module_uuid)

        if warning_type == 1:
            self.check_to_update_firmware(module_id)
        elif warning_type == 2:
            # Note that more than one warning type 2 message can be received
            if self.update_in_progress:
                self.add_to_waitlist(module_id, module_type)
            else:
                self.update_module(module_id, module_type)
