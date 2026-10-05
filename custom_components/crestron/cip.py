"""CIP (Crestron Internet Protocol) async client.

Implements the TCP-based CIP protocol for communicating with Crestron
processors. Handles registration, heartbeat, and bidirectional join
updates for digital, analog, and serial join types.
"""

import asyncio
import logging
import ssl
import struct
from collections.abc import Callable

_LOGGER = logging.getLogger(__name__)

# CIP packet types
PKT_REGISTER = 0x01
PKT_REGISTER_ACK = 0x02
PKT_DATA = 0x05
PKT_HEARTBEAT = 0x0D
PKT_HEARTBEAT_ACK = 0x0E
PKT_END_OF_QUERY = 0x0F

# Join data type markers within a DATA packet payload
JOIN_DIGITAL = 0x00
JOIN_ANALOG = 0x14
JOIN_SERIAL = 0x15

# Registration payload template — IPID is inserted at index 5
_REG_PREFIX = bytes([0x40, 0x00, 0x00, 0x00, 0x00])
_REG_SUFFIX = bytes([0xFF])


class CipClient:
    """Async CIP protocol client for Crestron processors."""

    def __init__(
        self,
        host: str,
        port: int,
        ipid: int,
        use_ssl: bool = False,
        on_digital: Callable[[int, bool], None] | None = None,
        on_analog: Callable[[int, int], None] | None = None,
        on_serial: Callable[[int, str], None] | None = None,
        on_connect: Callable[[], None] | None = None,
        on_disconnect: Callable[[], None] | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._ipid = ipid
        self._use_ssl = use_ssl
        self._on_digital = on_digital
        self._on_analog = on_analog
        self._on_serial = on_serial
        self._on_connect = on_connect
        self._on_disconnect = on_disconnect

        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._connected = False
        self._registered = False
        self._buffer = bytearray()
        self._read_task: asyncio.Task | None = None
        self._reconnect_task: asyncio.Task | None = None
        self._closing = False

        self.digital_states: dict[int, bool] = {}
        self.analog_states: dict[int, int] = {}
        self.serial_states: dict[int, str] = {}

    @property
    def connected(self) -> bool:
        return self._connected and self._registered

    async def connect(self) -> bool:
        """Connect and register with the Crestron processor."""
        try:
            ssl_context = None
            if self._use_ssl:
                ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE

            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(
                    self._host, self._port, ssl=ssl_context
                ),
                timeout=10,
            )
            self._connected = True
            self._buffer.clear()

            await self._send_register()
            self._read_task = asyncio.create_task(self._read_loop())

            for _ in range(30):
                if self._registered:
                    break
                await asyncio.sleep(0.1)

            if not self._registered:
                _LOGGER.warning(
                    "Connected to %s:%d but registration not acknowledged",
                    self._host,
                    self._port,
                )
                return False

            _LOGGER.info(
                "Connected to Crestron processor at %s:%d (IPID 0x%02X)",
                self._host,
                self._port,
                self._ipid,
            )
            await self._send_update_request()
            if self._on_connect:
                self._on_connect()
            return True

        except Exception:
            _LOGGER.exception(
                "Failed to connect to %s:%d", self._host, self._port
            )
            self._connected = False
            return False

    async def disconnect(self) -> None:
        """Disconnect from the processor."""
        self._closing = True
        self._connected = False
        self._registered = False

        for task in (self._read_task, self._reconnect_task):
            if task:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        self._read_task = None
        self._reconnect_task = None

        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
            self._writer = None
            self._reader = None

    # ── send helpers ──────────────────────────────────────────────

    async def _send_packet(self, ptype: int, payload: bytes = b"") -> None:
        if not self._writer:
            return
        header = struct.pack(">BH", ptype, len(payload))
        try:
            self._writer.write(header + payload)
            await self._writer.drain()
        except Exception:
            _LOGGER.debug("Send failed for packet 0x%02X", ptype)
            await self._handle_disconnect()

    async def _send_register(self) -> None:
        payload = _REG_PREFIX + bytes([self._ipid]) + _REG_SUFFIX
        await self._send_packet(PKT_REGISTER, payload)
        _LOGGER.debug("Sent registration for IPID 0x%02X", self._ipid)

    async def _send_heartbeat_ack(self) -> None:
        await self._send_packet(PKT_HEARTBEAT_ACK)

    async def _send_update_request(self) -> None:
        payload = bytes([0x00, 0x00, 0x02, 0x00, self._ipid])
        await self._send_packet(PKT_DATA, payload)
        _LOGGER.debug("Sent update request for all join states")

    # ── public join setters (1-based join numbers) ────────────────

    async def set_digital(self, join: int, value: bool) -> None:
        """Set a digital join (1-based)."""
        j = join - 1
        b1 = (j >> 7) & 0xFF
        b2 = (j & 0x7F) | (0x80 if value else 0x00)
        await self._send_packet(PKT_DATA, bytes([JOIN_DIGITAL, b1, b2]))
        self.digital_states[join] = value
        _LOGGER.debug("Set digital %d = %s", join, value)

    async def pulse_digital(self, join: int) -> None:
        """Pulse a digital join high then low."""
        await self.set_digital(join, True)
        await asyncio.sleep(0.1)
        await self.set_digital(join, False)

    async def set_analog(self, join: int, value: int) -> None:
        """Set an analog join (1-based, 0–65535)."""
        j = join - 1
        payload = struct.pack(">BHH", JOIN_ANALOG, j, value)
        await self._send_packet(PKT_DATA, payload)
        self.analog_states[join] = value
        _LOGGER.debug("Set analog %d = %d", join, value)

    async def set_serial(self, join: int, value: str) -> None:
        """Set a serial join string (1-based)."""
        j = join - 1
        payload = struct.pack(">BH", JOIN_SERIAL, j) + value.encode("utf-8")
        await self._send_packet(PKT_DATA, payload)
        self.serial_states[join] = value
        _LOGGER.debug("Set serial %d = %s", join, value)

    # ── receive loop ──────────────────────────────────────────────

    async def _read_loop(self) -> None:
        try:
            while self._connected and self._reader:
                data = await self._reader.read(4096)
                if not data:
                    _LOGGER.info("Connection closed by processor")
                    await self._handle_disconnect()
                    return
                self._buffer.extend(data)
                self._process_buffer()
        except asyncio.CancelledError:
            raise
        except ConnectionResetError:
            _LOGGER.warning("Connection reset by processor")
            await self._handle_disconnect()
        except Exception:
            _LOGGER.exception("CIP read loop error")
            await self._handle_disconnect()

    def _process_buffer(self) -> None:
        while len(self._buffer) >= 3:
            ptype = self._buffer[0]
            length = struct.unpack(">H", self._buffer[1:3])[0]
            if len(self._buffer) < 3 + length:
                break
            payload = bytes(self._buffer[3 : 3 + length])
            del self._buffer[: 3 + length]
            self._handle_packet(ptype, payload)

    def _handle_packet(self, ptype: int, payload: bytes) -> None:
        if ptype == PKT_REGISTER_ACK:
            self._registered = True
            _LOGGER.debug("Registration acknowledged")

        elif ptype == PKT_DATA:
            self._parse_data(payload)

        elif ptype == PKT_HEARTBEAT:
            asyncio.create_task(self._send_heartbeat_ack())

        elif ptype == PKT_END_OF_QUERY:
            _LOGGER.debug("End of query — all states received")

        else:
            _LOGGER.debug(
                "Unknown packet type 0x%02X (%d bytes)", ptype, len(payload)
            )

    def _parse_data(self, payload: bytes) -> None:
        if len(payload) < 3:
            return
        dtype = payload[0]

        if dtype < JOIN_ANALOG:
            b1 = payload[1]
            b2 = payload[2]
            join = ((dtype << 7) | (b1 << 7) | (b2 & 0x7F)) + 1
            if dtype == JOIN_DIGITAL:
                join = ((b1 << 7) | (b2 & 0x7F)) + 1
            state = bool(b2 & 0x80)
            self.digital_states[join] = state
            if self._on_digital:
                self._on_digital(join, state)
            _LOGGER.debug("Rx digital %d = %s", join, state)

        elif dtype == JOIN_ANALOG and len(payload) >= 5:
            j = struct.unpack(">H", payload[1:3])[0]
            val = struct.unpack(">H", payload[3:5])[0]
            join = j + 1
            self.analog_states[join] = val
            if self._on_analog:
                self._on_analog(join, val)
            _LOGGER.debug("Rx analog %d = %d", join, val)

        elif dtype == JOIN_SERIAL and len(payload) >= 3:
            j = struct.unpack(">H", payload[1:3])[0]
            text = payload[3:].decode("utf-8", errors="replace")
            join = j + 1
            self.serial_states[join] = text
            if self._on_serial:
                self._on_serial(join, text)
            _LOGGER.debug("Rx serial %d = '%s'", join, text)

        else:
            _LOGGER.debug("Unknown data type 0x%02X: %s", dtype, payload.hex())

    # ── reconnection ──────────────────────────────────────────────

    async def _handle_disconnect(self) -> None:
        was_connected = self._connected
        self._connected = False
        self._registered = False

        if self._writer:
            try:
                self._writer.close()
            except Exception:
                pass
            self._writer = None
            self._reader = None

        if was_connected and not self._closing:
            if self._on_disconnect:
                self._on_disconnect()
            self._reconnect_task = asyncio.create_task(self._reconnect_loop())

    async def _reconnect_loop(self) -> None:
        delays = [5, 10, 30, 60, 60]
        attempt = 0
        while not self._closing:
            delay = delays[min(attempt, len(delays) - 1)]
            _LOGGER.info(
                "Reconnecting to %s:%d in %ds (attempt %d)",
                self._host,
                self._port,
                delay,
                attempt + 1,
            )
            await asyncio.sleep(delay)
            if self._closing:
                break
            if await self.connect():
                return
            attempt += 1
