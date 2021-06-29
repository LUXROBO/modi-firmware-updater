import io
import json
import pathlib
import sys
import time
import urllib.request as ur
import zipfile
from base64 import b64decode, b64encode
from io import open
from os import path
from urllib.error import URLError

import serial

from modi_firmware_updater.util.connection_util import list_modi_ports


def retry(exception_to_catch):
    def decorator(func):
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except exception_to_catch:
                return wrapper(*args, **kwargs)

        return wrapper

    return decorator

class ESP32FirmwareUpdater(serial.Serial):
    DEVICE_READY = 0x2B
    DEVICE_SYNC = 0x08
    SPI_ATTACH_REQ = 0xD
    SPI_FLASH_SET = 0xB
    ESP_FLASH_BEGIN = 0x02
    ESP_FLASH_DATA = 0x03
    ESP_FLASH_END = 0x04

    ESP_FLASH_BLOCK = 0x200
    ESP_FLASH_CHUNK = 0x4000
    ESP_CHECKSUM_MAGIC = 0xEF

    def __init__(self):
        modi_ports = list_modi_ports()
        if not modi_ports:
            raise serial.SerialException("No MODI port is connected")
        for modi_port in modi_ports:
            try:
                super().__init__(
                    modi_port.device, timeout=0.1, baudrate=921600
                )
            except Exception:
                print('Next network module')
                continue
        print(f"Connecting to MODI network module at {modi_port.device}")

        self.__address = [0x1000, 0x8000, 0xD000, 0x10000, 0xD0000]
        self.file_path = [
            "bootloader.bin",
            "partitions.bin",
            "ota_data_initial.bin",
            "modi_ota_factory.bin",
            "esp32.bin",
        ]
        self.id = None
        self.version = None
        self.__version_to_update = None

        self.update_in_progress = False
        self.ui = None

    def set_ui(self, ui):
        self.ui = ui

    def update_firmware(self, force=False):
        print("Turning interpreter off...")
        self.write(b'{"c":160,"s":0,"d":18,"b":"AAMAAAAA","l":6}')

        self.update_in_progress = True
        self.__boot_to_app()
        self.__version_to_update = self.__get_latest_version()
        self.id = self.__get_esp_id()
        self.version = self.__get_esp_version()
        if self.version and self.version == self.__version_to_update:
            if not force and not self.ui:
                response = input(
                    f"ESP version already up to date (v{self.version})."
                    f" Do you still want to proceed? [y/n]: "
                )
                if "y" not in response:
                    return

        print(f"Updating v{self.version} to v{self.__version_to_update}")
        firmware_buffer = self.__compose_binary_firmware()

        self.__device_ready()
        self.__device_sync()
        self.__flash_attach()
        self.__set_flash_param()
        manager = None

        self.__write_binary_firmware(firmware_buffer, manager)
        print("Booting to application...")
        self.__wait_for_json()
        self.__boot_to_app()
        time.sleep(1)
        self.__set_esp_version(self.__version_to_update)
        print("ESP firmware update is complete!!")

        time.sleep(1)
        self.update_in_progress = False
        self.flushInput()
        self.flushOutput()
        self.close()

        if self.ui:
            self.ui.update_stm32_modules.setStyleSheet(
                f"border-image: url({self.ui.active_path}); font-size: 16px"
            )
            self.ui.update_stm32_modules.setEnabled(True)
            self.ui.update_network_stm32.setStyleSheet(
                f"border-image: url({self.ui.active_path}); font-size: 16px"
            )
            self.ui.update_network_stm32.setEnabled(True)
            if self.ui.is_english:
                self.ui.update_network_esp32.setText("Update Network ESP32")
            else:
                self.ui.update_network_esp32.setText("네트워크 모듈 업데이트")

    def __device_ready(self):
        print("Redirecting connection to esp device...")
        self.write(b'{"c":43,"s":0,"d":4095,"b":"AA==","l":1}')

    def __device_sync(self):
        print("Syncing the esp device...")
        sync_pkt = self.__parse_pkt(
            [0x0, self.DEVICE_SYNC, 0x24, 0, 0, 0, 0, 0, 0x7, 0x7, 0x12, 0x20]
            + 32 * [0x55]
        )
        self.__send_pkt(sync_pkt, timeout=10, continuous=True)
        print("Sync Complete")

    def __flash_attach(self):
        print("Attaching flash to esp device..")
        attach_pkt = self.__parse_pkt(
            [0x0, self.SPI_ATTACH_REQ, 0x8] + 13 * [0]
        )
        self.__send_pkt(attach_pkt, timeout=10)
        print("Flash attach Complete")

    def __set_flash_param(self):
        print("Setting esp flash parameter...")
        param_data = [0] * 32
        fl_id, total_size, block_size, sector_size, page_size, status_mask = (
            0,
            2 * 1024 * 1024,
            64 * 1024,
            4 * 1024,
            256,
            0xFFFF,
        )
        param_data[1] = self.SPI_FLASH_SET
        param_data[2] = 0x18
        param_data[8:12] = int.to_bytes(fl_id, length=4, byteorder="little")
        param_data[12:16] = int.to_bytes(
            total_size, length=4, byteorder="little"
        )
        param_data[16:20] = int.to_bytes(
            block_size, length=4, byteorder="little"
        )
        param_data[20:24] = int.to_bytes(
            sector_size, length=4, byteorder="little"
        )
        param_data[24:28] = int.to_bytes(
            page_size, length=4, byteorder="little"
        )
        param_data[28:32] = int.to_bytes(
            status_mask, length=4, byteorder="little"
        )
        param_pkt = self.__parse_pkt(param_data)
        self.__send_pkt(param_pkt, timeout=10)
        print("Parameter set complete")

    @staticmethod
    def __parse_pkt(data):
        pkt = bytes(data)
        pkt = pkt.replace(b"\xdb", b"\xdb\xdd").replace(b"\xc0", b"\xdb\xdc")
        pkt = b"\xc0" + pkt + b"\xc0"
        return pkt

    @retry(Exception)
    def __send_pkt(self, pkt, wait=True, timeout=None, continuous=False):
        self.write(pkt)
        self.reset_input_buffer()
        if wait:
            cmd = bytearray(pkt)[2]
            init_time = time.time()
            while not timeout or time.time() - init_time < timeout:
                if continuous:
                    time.sleep(0.1)
                else:
                    time.sleep(0.01)
                recv_pkt = self.__read_slip()
                if not recv_pkt:
                    if continuous:
                        self.__send_pkt(pkt, wait=False)
                    continue
                recv_cmd = bytearray(recv_pkt)[2]
                if cmd == recv_cmd:
                    if bytearray(recv_pkt)[1] != 0x01:
                        raise Exception
                    return True
                elif continuous:
                    self.__send_pkt(pkt, wait=False)
            print("Sending Again...")
            raise Exception("Timeout Expired!")

    def __read_slip(self):
        slip_pkt = b""
        while slip_pkt != b"\xc0":
            slip_pkt = self.read()
            if slip_pkt == b"":
                return b""
        slip_pkt += self.read_until(b"\xc0")
        return slip_pkt

    def __read_json(self):
        json_pkt = b""
        while json_pkt != b"{":
            json_pkt = self.read()
            if json_pkt == b"":
                return ""
            time.sleep(0.1)
        json_pkt += self.read_until(b"}")
        return json_pkt

    def __wait_for_json(self):
        json_msg = self.__read_json()
        while not json_msg:
            json_msg = self.__read_json()
            time.sleep(0.1)
        return json_msg

    def __get_esp_id(self):
        json_msg = json.loads(self.__wait_for_json())
        while json_msg["c"] != 0:
            json_msg = json.loads(self.__wait_for_json())
        return json_msg["s"]

    def __get_esp_version(self):
        get_version_pkt = b'{"c":160,"s":25,"d":4095,"b":"AAAAAAAAAA==","l":8}'
        self.write(get_version_pkt)
        json_msg = json.loads(self.__wait_for_json())
        init_time = time.time()
        while json_msg["c"] != 0xA1:
            self.write(get_version_pkt)
            json_msg = json.loads(self.__wait_for_json())
            if time.time() - init_time > 1:
                return None
        ver = b64decode(json_msg["b"]).lstrip(b"\x00")
        return ver.decode("ascii")

    def __set_esp_version(self, version_text: str):
        print(f"Writing version info (v{version_text})")
        version_byte = version_text.encode("ascii")
        version_byte = b"\x00" * (8 - len(version_byte)) + version_byte
        version_text = b64encode(version_byte).decode("utf8")
        version_msg = (
            "{" + f'"c":160,"s":24,"d":4095,'
            f'"b":"{version_text}","l":8' + "}"
        )
        version_msg_enc = version_msg.encode("utf8")
        self.write(version_msg_enc)

        while json.loads(self.__wait_for_json())["c"] != 0xA1:
            time.sleep(0.5)
            self.__boot_to_app()
            self.write(version_msg.encode("utf8"))
        print("The version info has been set!!")

    def __compose_binary_firmware(self):
        binary_firmware = b""
        for i, bin_path in enumerate(self.file_path):
            if self.ui:
                if i == 2:
                    if sys.platform.startswith("win"):
                        root_path = pathlib.PurePosixPath(
                            pathlib.PurePath(__file__),
                            "..",
                            "..",
                            "assets",
                            "firmware",
                            "esp32",
                        )
                    else:
                        root_path = path.join(
                            path.dirname(__file__),
                            "..",
                            "assets",
                            "firmware",
                            "esp32",
                        )
                elif i == 3:
                    root_path = (
                        "https://download.luxrobo.com/modi-ota-firmware/"
                        "ota.zip"
                    )
                else:
                    root_path = (
                        "https://download.luxrobo.com/modi-esp32-firmware/"
                        "esp.zip"
                    )

                if i != 2:
                    try:
                        with ur.urlopen(root_path, timeout=5) as conn:
                            download_response = conn.read()
                    except URLError:
                        raise URLError(
                            "Failed to download firmware. Check your internet."
                        )
                    zip_content = zipfile.ZipFile(
                        io.BytesIO(download_response), "r"
                    )
                    bin_data = zip_content.read(bin_path)
                elif i == 2:
                    if sys.platform.startswith("win"):
                        firmware_path = pathlib.PurePosixPath(
                            root_path, bin_path
                        )
                    else:
                        firmware_path = path.join(root_path, bin_path)
                    if self.ui.installation:
                        firmware_path = path.dirname(__file__).replace(
                            "core", bin_path
                        )
                    with open(firmware_path, "rb") as bin_file:
                        bin_data = bin_file.read()
            else:
                root_path = path.join(
                    path.dirname(__file__), "..", "assets", "firmware", "esp32"
                )
                firmware_path = path.join(root_path, bin_path)
                with open(firmware_path, "rb") as bin_file:
                    bin_data = bin_file.read()
            binary_firmware += bin_data
            if i < len(self.__address) - 1:
                binary_firmware += b"\xFF" * (
                    self.__address[i + 1] - self.__address[i] - len(bin_data)
                )
        return binary_firmware

    def __get_latest_version(self):
        if self.ui:
            version_path = (
                "https://download.luxrobo.com/modi-esp32-firmware/version.txt"
            )
            version_info = None
            for line in ur.urlopen(version_path, timeout=5):
                version_info = line.decode("utf-8").lstrip("v").rstrip("\n")
        else:
            root_path = path.join(
                path.dirname(__file__), "..", "assets", "firmware", "esp32"
            )
            version_path = path.join(root_path, "esp_version.txt")
            with open(version_path, "r") as version_file:
                version_info = version_file.readline().lstrip("v").rstrip("\n")
        return version_info

    def __erase_chunk(self, size, offset):
        num_blocks = size // self.ESP_FLASH_BLOCK + 1
        erase_data = [0] * 24
        erase_data[1] = self.ESP_FLASH_BEGIN
        erase_data[2] = 0x10
        erase_data[8:12] = int.to_bytes(size, length=4, byteorder="little")
        erase_data[12:16] = int.to_bytes(
            num_blocks, length=4, byteorder="little"
        )
        erase_data[16:20] = int.to_bytes(
            self.ESP_FLASH_BLOCK, length=4, byteorder="little"
        )
        erase_data[20:24] = int.to_bytes(offset, length=4, byteorder="little")
        erase_pkt = self.__parse_pkt(erase_data)
        self.__send_pkt(erase_pkt, timeout=10)

    def __write_flash_block(self, data, seq_block):
        size = len(data)
        block_data = [0] * (size + 24)
        checksum = self.ESP_CHECKSUM_MAGIC

        block_data[1] = self.ESP_FLASH_DATA
        block_data[2:4] = int.to_bytes(size + 16, length=2, byteorder="little")
        block_data[8:12] = int.to_bytes(size, length=4, byteorder="little")
        block_data[12:16] = int.to_bytes(
            seq_block, length=4, byteorder="little"
        )
        for i in range(size):
            block_data[24 + i] = data[i]
            checksum ^= 0xFF & data[i]
        block_data[4:8] = int.to_bytes(checksum, length=4, byteorder="little")
        block_pkt = self.__parse_pkt(block_data)
        self.__send_pkt(block_pkt)

    def __write_binary_firmware(self, binary_firmware: bytes, manager):
        chunk_queue = []
        num_blocks = len(binary_firmware) // self.ESP_FLASH_BLOCK + 1
        while binary_firmware:
            if self.ESP_FLASH_CHUNK < len(binary_firmware):
                chunk_queue.append(binary_firmware[: self.ESP_FLASH_CHUNK])
                binary_firmware = binary_firmware[self.ESP_FLASH_CHUNK :]
            else:
                chunk_queue.append(binary_firmware[:])
                binary_firmware = b""

        blocks_downloaded = 0
        print("Start uploading firmware data...")
        for seq, chunk in enumerate(chunk_queue):
            self.__erase_chunk(
                len(chunk), self.__address[0] + seq * self.ESP_FLASH_CHUNK
            )
            blocks_downloaded += self.__write_chunk(
                chunk, blocks_downloaded, num_blocks, manager
            )
        if manager:
            manager.quit()
        if self.ui:
            if self.ui.is_english:
                self.ui.update_network_esp32.setText(
                    "Network ESP32 update is in progress. (100%)"
                )
            else:
                self.ui.update_network_esp32.setText(
                    "네트워크 모듈 업데이트가 진행중입니다. (100%)"
                )
        print(f"\r{self.__progress_bar(1, 1)}")
        print("Firmware Upload Complete")

    def __write_chunk(self, chunk, curr_seq, total_seq, manager):
        block_queue = []
        while chunk:
            if self.ESP_FLASH_BLOCK < len(chunk):
                block_queue.append(chunk[: self.ESP_FLASH_BLOCK])
                chunk = chunk[self.ESP_FLASH_BLOCK :]
            else:
                block_queue.append(chunk[:])
                chunk = b""
        for seq, block in enumerate(block_queue):
            if manager:
                manager.status = self.__progress_bar(curr_seq + seq, total_seq)
            if self.ui:
                if self.ui.is_english:
                    self.ui.update_network_esp32.setText(
                        f"Network ESP32 update is in progress. "
                        f"({int((curr_seq+seq)/total_seq*100)}%)"
                    )
                else:
                    self.ui.update_network_esp32.setText(
                        f"네트워크 모듈 업데이트가 진행중입니다. "
                        f"({int((curr_seq+seq)/total_seq*100)}%)"
                    )
            print(
                f"\r{self.__progress_bar(curr_seq + seq, total_seq)}", end=""
            )
            self.__write_flash_block(block, seq)
        return len(block_queue)

    def __boot_to_app(self):
        self.write(b'{"c":160,"s":0,"d":174,"b":"AAAAAAAAAA==","l":8}')

    @staticmethod
    def __progress_bar(current: int, total: int) -> str:
        curr_bar = 50 * current // total
        rest_bar = 50 - curr_bar
        return (
            f"Firmware Upload: [{'=' * curr_bar}>{'.' * rest_bar}] "
            f"{100 * current / total:3.1f}%"
        )
