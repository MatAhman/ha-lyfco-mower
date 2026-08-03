#!/usr/bin/env python3
"""Provision a Lyfco/Miotlink mower from its local Wi-Fi access point.

Connect the computer manually to the mower AP (usually MLinkAp_* or
MiotLinkAp_*) before running this script. Discovery is read-only. Configuration
is only sent after an explicit command and interactive confirmation.
"""

from __future__ import annotations

import argparse
from collections import OrderedDict
import getpass
import platform
import random
import re
import socket
import subprocess
import sys
import time


MOWER_IP = "192.168.4.1"
DEVICE_PORT = 64536
VSP_HEADER_SIZE = 20
SEARCH_RETRIES = 5
RESPONSE_TIMEOUT = 2.0


class SetupError(Exception):
    """Expected provisioning error."""


def build_vsp(payload: str) -> bytes:
    """Wrap a Miotlink text payload in the app's VSP frame."""
    raw = payload.encode("latin-1")
    total = len(raw) + VSP_HEADER_SIZE
    frame = bytearray(total)
    frame[0:2] = b"0h"
    frame[2:4] = total.to_bytes(2, "big")
    frame[8] = 0x65
    frame[10:12] = (total - 8).to_bytes(2, "big")
    frame[15] = 1
    frame[VSP_HEADER_SIZE:] = raw
    for index in range(8, total):
        frame[index] ^= 0x30
    return bytes(frame)


def decode_vsp(datagram: bytes) -> str:
    """Decode one UDP VSP datagram."""
    if len(datagram) < VSP_HEADER_SIZE or datagram[0:2] != b"0h":
        raise SetupError("The response does not contain a valid VSP header")
    declared = int.from_bytes(datagram[2:4], "big")
    # This mower's SearchAck declares 256 bytes but transmits 255. The Android
    # app ignores the declared size and decodes the received datagram. Tolerate
    # that single-byte firmware bug, but reject larger truncations.
    if declared < VSP_HEADER_SIZE or declared > len(datagram) + 1:
        raise SetupError("The response contains an invalid VSP length")
    available = min(declared, len(datagram))
    decoded = bytearray(datagram[:available])
    for index in range(8, available):
        decoded[index] ^= decoded[0]
    return bytes(decoded[VSP_HEADER_SIZE:]).decode("latin-1")


def parse_fields(message: str) -> OrderedDict[str, str]:
    """Parse the ampersand-separated Miotlink key/value format."""
    fields: OrderedDict[str, str] = OrderedDict()
    for item in message.split("&"):
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        fields[key] = value
    return fields


def validate_text(value: str, label: str, allow_empty: bool = False) -> str:
    value = value.strip()
    if not value and not allow_empty:
        raise SetupError(f"{label} must not be empty")
    if any(character in value for character in "&=\r\n"):
        raise SetupError(f"{label} must not contain &, =, or line breaks")
    try:
        value.encode("latin-1")
    except UnicodeEncodeError as error:
        raise SetupError(
            f"{label} contains characters that the mower protocol cannot encode"
        ) from error
    return value


def normalize_mac(value: str) -> str:
    """Validate and normalize a BSSID/MAC address."""
    value = value.strip().replace("-", ":").upper()
    if not re.fullmatch(r"(?:[0-9A-F]{2}:){5}[0-9A-F]{2}", value):
        raise SetupError(f"Invalid BSSID/MAC address: {value!r}")
    return value


def windows_wifi_details() -> tuple[str | None, str | None]:
    """Read current AP SSID and BSSID using Windows netsh when available."""
    if platform.system() != "Windows":
        return None, None
    try:
        result = subprocess.run(
            ["netsh", "wlan", "show", "interfaces"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None, None
    ssid = None
    bssid = None
    for line in result.stdout.splitlines():
        match = re.match(r"\s*SSID\s*:\s*(.+?)\s*$", line, re.IGNORECASE)
        if match and not line.lstrip().upper().startswith("BSSID"):
            ssid = match.group(1)
        match = re.match(r"\s*BSSID\s*:\s*(.+?)\s*$", line, re.IGNORECASE)
        if match:
            bssid = match.group(1)
    return ssid, bssid


def make_socket(requested_port: int | None = None) -> socket.socket:
    """Bind the random UDP receive port used by the AP setup flow."""
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    udp.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    candidates = (
        [requested_port]
        if requested_port is not None
        else [random.randint(30000, 32999) for _ in range(20)]
    )
    last_error: OSError | None = None
    for port in candidates:
        try:
            udp.bind(("0.0.0.0", port))
            break
        except OSError as error:
            last_error = error
    else:
        udp.close()
        raise SetupError(f"Could not open a local UDP port: {last_error}")
    return udp


def route_local_ip() -> str:
    """Return the local IPv4 address selected for the mower route."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect((MOWER_IP, DEVICE_PORT))
        return probe.getsockname()[0]
    except OSError:
        return "unknown"
    finally:
        probe.close()


def drain_socket(udp: socket.socket) -> None:
    """Discard old datagrams before a new request."""
    udp.setblocking(False)
    try:
        while True:
            udp.recvfrom(65535)
    except BlockingIOError:
        pass
    finally:
        udp.setblocking(True)


def send_request(udp: socket.socket, payload: str) -> None:
    """Send directly and as broadcast, matching the app's AP discovery."""
    frame = build_vsp(payload)
    errors: list[OSError] = []
    for destination in (MOWER_IP, "255.255.255.255"):
        try:
            udp.sendto(frame, (destination, DEVICE_PORT))
        except OSError as error:
            errors.append(error)
    if len(errors) == 2:
        raise SetupError(f"Could not send the UDP packet: {errors[-1]}")


def wait_for(
    udp: socket.socket, prefix: str, timeout: float = RESPONSE_TIMEOUT
) -> tuple[str, tuple[str, int]] | None:
    """Wait for one decoded Miotlink message with the requested prefix."""
    deadline = time.monotonic() + timeout
    while (remaining := deadline - time.monotonic()) > 0:
        udp.settimeout(remaining)
        try:
            datagram, address = udp.recvfrom(65535)
        except socket.timeout:
            return None
        try:
            message = decode_vsp(datagram)
        except SetupError:
            continue
        if message.startswith(prefix):
            return message, address
    return None


def discover(udp: socket.socket) -> tuple[str, OrderedDict[str, str]]:
    """Perform read-only AP discovery and return SearchAck fields."""
    drain_socket(udp)
    local_port = udp.getsockname()[1]
    search = f"CodeName=Search&port={local_port}"
    for attempt in range(1, SEARCH_RETRIES + 1):
        print(f"Searching for the mower ({attempt}/{SEARCH_RETRIES})...")
        send_request(udp, search)
        response = wait_for(udp, "CodeName=SearchAck")
        if response is not None:
            message, address = response
            fields = parse_fields(message)
            print(f"The mower replied from {address[0]}:{address[1]}")
            return message, fields
    raise SetupError(
        "No SearchAck was received. Make sure the computer is connected to "
        "the mower's MLinkAp_/MiotLinkAp_ network and the firewall allows UDP."
    )


def show_discovery(fields: OrderedDict[str, str]) -> None:
    """Print useful non-secret discovery fields."""
    print("\nDiscovered parameters:")
    for key, value in fields.items():
        if key in {"StaPd", "ApPd"} and value:
            value = "********"
        print(f"  {key}: {value}")


def build_set_wifi(
    search_fields: OrderedDict[str, str],
    home_ssid: str,
    home_password: str,
    ap_ssid: str,
    ap_bssid: str,
    response_port: int,
) -> str:
    """Build SetWifi from SearchAck plus the fields used by the app."""
    fields = OrderedDict(search_fields)
    fields.pop("CodeName", None)
    fields.pop("device_name", None)
    fields.pop("msg", None)
    fields.pop("port", None)
    fields["UartInfo"] = "0`115200`8`0`256`100"
    fields["Mac"] = normalize_mac(ap_bssid)
    fields["ApId"] = validate_text(ap_ssid, "Mower AP SSID")
    fields["StaId"] = validate_text(home_ssid, "Wi-Fi SSID")
    fields["StaPd"] = validate_text(
        home_password, "Wi-Fi password", allow_empty=True
    )
    fields["Mode"] = "2"
    fields["port"] = str(response_port)
    return "CodeName=SetWifi&" + "&".join(
        f"{key}={value}" for key, value in fields.items()
    )


def configure(
    udp: socket.socket,
    fields: OrderedDict[str, str],
    args: argparse.Namespace,
) -> None:
    """Interactively confirm and send SetWifi."""
    detected_ssid, detected_bssid = windows_wifi_details()
    ap_ssid = args.ap_ssid or fields.get("ApId") or detected_ssid
    ap_bssid = args.ap_bssid or fields.get("Mac") or detected_bssid
    if not ap_ssid or not ap_bssid:
        raise SetupError(
            "Could not determine the mower AP SSID/BSSID automatically. "
            "Provide them with --ap-ssid and --ap-bssid."
        )
    if not (ap_ssid.startswith("MLinkAp_") or ap_ssid.startswith("MiotLinkAp_")):
        print(f"WARNING: the connected network does not look like a mower AP: {ap_ssid}")
    home_ssid = validate_text(args.ssid, "Wi-Fi SSID")
    home_password = args.password
    if home_password is None:
        home_password = getpass.getpass("Home Wi-Fi password: ")
    payload = build_set_wifi(
        fields,
        home_ssid,
        home_password,
        ap_ssid,
        ap_bssid,
        udp.getsockname()[1],
    )

    print("\nConfiguration to be sent:")
    print(f"  Mower AP:   {ap_ssid} ({normalize_mac(ap_bssid)})")
    print(f"  Home Wi-Fi: {home_ssid}")
    print(f"  Password:   {'(empty)' if not home_password else '********'}")
    print("  Mode:       2 (connect to Wi-Fi)")
    print("\nThe mower AP may disappear while the mower connects.")
    confirmation = input("Type CONFIGURE to continue: ").strip()
    if confirmation != "CONFIGURE":
        raise SetupError("Aborted; no Wi-Fi settings were sent")

    drain_socket(udp)
    for attempt in range(1, 6):
        print(f"Sending SetWifi ({attempt}/5)...")
        send_request(udp, payload)
        response = wait_for(udp, "CodeName=SetWifiAck", timeout=1.5)
        if response is not None:
            message, address = response
            print(f"SetWifiAck from {address[0]}:{address[1]}: {message}")
            print("The settings were accepted. Wait approximately 30–60 seconds.")
            return
    print(
        "No SetWifiAck was received. The mower may still have accepted the "
        "command and closed its AP. Wait 60 seconds and check the router client list."
    )


def self_test() -> None:
    """Run deterministic local protocol tests without network access."""
    sample = (
        "CodeName=SearchAck&DevName=M10_1.5.4&ByName=M10&"
        "Mip=2`192.168.4.1&ApPd=&UartInfo=0`115200`8`0`256`100&"
        "tInfo=5`9600`192.168.1.100&cInfo=1`9600`192.168.1.100&MbInfo=0"
    )
    assert decode_vsp(build_vsp(sample)) == sample
    truncated = bytearray(build_vsp(sample))
    truncated[2:4] = (len(truncated) + 1).to_bytes(2, "big")
    assert decode_vsp(bytes(truncated)) == sample
    payload = build_set_wifi(
        parse_fields(sample),
        "TestWifi",
        "hemligt",
        "MLinkAp_TEST",
        "AA:BB:CC:DD:EE:FF",
        31234,
    )
    parsed = parse_fields(payload)
    assert parsed["CodeName"] == "SetWifi"
    assert parsed["StaId"] == "TestWifi"
    assert parsed["StaPd"] == "hemligt"
    assert parsed["Mode"] == "2"
    assert parsed["port"] == "31234"
    print("Self-test: OK")


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Discover or provision a Lyfco mower through its Wi-Fi AP."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    discover_parser = subparsers.add_parser(
        "discover", help="Read SearchAck only; does not change anything"
    )
    discover_parser.add_argument(
        "--listen-port",
        type=int,
        help="Local UDP port; normally selected automatically from 30000–32999",
    )
    configure_parser = subparsers.add_parser(
        "configure", help="Send home Wi-Fi settings after confirmation"
    )
    configure_parser.add_argument("--ssid", required=True, help="Home 2.4 GHz SSID")
    configure_parser.add_argument(
        "--password",
        help="Wi-Fi password; omit for hidden input (recommended)",
    )
    configure_parser.add_argument("--ap-ssid", help="Mower AP SSID")
    configure_parser.add_argument("--ap-bssid", help="Mower AP BSSID/MAC")
    configure_parser.add_argument(
        "--listen-port",
        type=int,
        help="Local UDP port; normally selected automatically from 30000–32999",
    )
    subparsers.add_parser("self-test", help="Test protocol code without a network")
    return parser


def main() -> int:
    args = make_parser().parse_args()
    try:
        if args.command == "self-test":
            self_test()
            return 0
        print(f"Expected mower address: {MOWER_IP}")
        listen_port = args.listen_port
        if listen_port is not None and not 1024 <= listen_port <= 65535:
            raise SetupError("--listen-port must be between 1024 and 65535")
        with make_socket(listen_port) as udp:
            print(f"Listening on {route_local_ip()}:{udp.getsockname()[1]}")
            _, fields = discover(udp)
            show_discovery(fields)
            if args.command == "configure":
                configure(udp, fields, args)
        return 0
    except (SetupError, KeyboardInterrupt) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
