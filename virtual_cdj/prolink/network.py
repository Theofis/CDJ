"""Cross-platform IPv4 interface selection for PRO DJ LINK tools."""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass

from .wire import format_mac, parse_mac


@dataclass(frozen=True, slots=True)
class NetworkInterface:
    name: str | None
    ip: str
    netmask: str | None
    broadcast: str
    mac: str | None


def _interfaces() -> tuple[NetworkInterface, ...]:
    """Enumerate interfaces when optional psutil is already available.

    No networking dependency is added for Phase 2.5.  Explicit ``--bind-ip``,
    ``--broadcast-ip`` and ``--mac`` remain a complete fallback.
    """

    try:
        import psutil  # type: ignore[import-not-found]
    except ImportError:
        return ()

    result: list[NetworkInterface] = []
    link_family = getattr(psutil, "AF_LINK", object())
    for name, addresses in psutil.net_if_addrs().items():
        mac = next(
            (
                item.address
                for item in addresses
                if item.family == link_family and item.address
            ),
            None,
        )
        for item in addresses:
            if item.family != socket.AF_INET:
                continue
            try:
                ip = str(ipaddress.IPv4Address(item.address))
            except ipaddress.AddressValueError:
                continue
            broadcast = item.broadcast
            if not broadcast and item.netmask:
                broadcast = str(
                    ipaddress.IPv4Network(
                        f"{ip}/{item.netmask}", strict=False
                    ).broadcast_address
                )
            result.append(
                NetworkInterface(
                    name=name,
                    ip=ip,
                    netmask=item.netmask,
                    broadcast=broadcast or "255.255.255.255",
                    mac=(format_mac(parse_mac(mac)) if mac else None),
                )
            )
    return tuple(result)


def resolve_network_interface(
    *,
    interface: str | None = "auto",
    bind_ip: str | None = None,
    broadcast_ip: str | None = None,
    mac: str | None = None,
) -> NetworkInterface:
    """Resolve a Windows/Linux interface name or IPv4 address safely."""

    requested = (interface or "auto").strip()
    explicit_ip = (
        str(ipaddress.IPv4Address(bind_ip.strip())) if bind_ip else None
    )
    available = _interfaces()

    matches: list[NetworkInterface]
    if explicit_ip:
        matches = [item for item in available if item.ip == explicit_ip]
        if requested.lower() != "auto":
            matches = [
                item
                for item in matches
                if item.name == requested or item.ip == requested
            ]
            if not matches and available:
                raise ValueError(
                    f"bind IP {explicit_ip} does not belong to interface {requested!r}"
                )
    elif requested.lower() == "auto":
        candidates = [
            item
            for item in available
            if not ipaddress.IPv4Address(item.ip).is_loopback
        ]
        link_local = [
            item
            for item in candidates
            if ipaddress.IPv4Address(item.ip).is_link_local
        ]
        matches = link_local if len(link_local) == 1 else candidates
        if len(matches) != 1:
            raise ValueError(
                "network interface is ambiguous; pass --interface or --bind-ip"
            )
    else:
        matches = [
            item
            for item in available
            if item.name == requested or item.ip == requested
        ]
        if not matches:
            try:
                explicit_ip = str(ipaddress.IPv4Address(requested))
            except ipaddress.AddressValueError as exc:
                raise ValueError(f"network interface not found: {requested!r}") from exc

    selected = matches[0] if matches else NetworkInterface(
        name=None,
        ip=explicit_ip or "",
        netmask=None,
        broadcast="255.255.255.255",
        mac=None,
    )
    if not selected.ip:
        raise ValueError("an explicit --bind-ip is required without interface discovery")

    resolved_broadcast = (
        str(ipaddress.IPv4Address(broadcast_ip))
        if broadcast_ip
        else selected.broadcast
    )
    resolved_mac = format_mac(parse_mac(mac)) if mac else selected.mac
    return NetworkInterface(
        name=selected.name,
        ip=selected.ip,
        netmask=selected.netmask,
        broadcast=resolved_broadcast,
        mac=resolved_mac,
    )


def mac_for_ip(ip: str, explicit: str | None = None) -> str | None:
    """Return the selected NIC's real MAC, never a fabricated fallback."""

    if explicit:
        return format_mac(parse_mac(explicit))
    wanted = str(ipaddress.IPv4Address(ip))
    return next((item.mac for item in _interfaces() if item.ip == wanted), None)


def broadcast_for_ip(ip: str) -> str:
    wanted = str(ipaddress.IPv4Address(ip))
    return next(
        (item.broadcast for item in _interfaces() if item.ip == wanted),
        "255.255.255.255",
    )
