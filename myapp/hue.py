# hue.py
"""
Hue API wrapper for controlling Philips Hue lights and rooms.

Functions:
    save_config(bridge_ip: str, username: str) -> None
    load_config() -> Dict[str, str]
    list_lights_detailed() -> Dict[int, Dict[str, Any]]
    set_on(light_id: int, on: bool) -> Any
    set_brightness(light_id: int, percent: int) -> Any
    set_color_hs(light_id: int, hue_degrees: float, sat_percent: float) -> Any
    list_rooms_detailed() -> Dict[int, Dict[str, Any]]
    set_room_on(group_id: int, on: bool) -> Any
    set_room_brightness(group_id: int, percent: int) -> Any
    set_room_color_hs(group_id: int, hue_degrees: float, sat_percent: float) -> Any
"""

import json
import os
from pathlib import Path
import requests
from typing import Dict, Any, List
import sys
import socket
import re
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed


def _primary_lan_prefixes() -> list[str]:
    """Best-effort find one or two local /24 prefixes like '192.168.86.'."""
    prefixes: list[str] = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        parts = ip.split(".")
        if len(parts) == 4:
            prefixes.append(".".join(parts[:3]) + ".")
    except Exception:
        pass
    # Optional: add common private ranges if we didn't find anything
    if not prefixes:
        prefixes = ["192.168.1.", "192.168.0.", "10.0.0."]
    return prefixes


def _probe_bridge_ip(ip: str, timeout: float = 0.5) -> str | None:
    """HTTP probe for Hue bridge at /description.xml (no auth needed)."""
    try:
        r = _session.get(f"http://{ip}/description.xml", timeout=timeout)
        if r.ok:
            txt = r.text.lower()
            # typical indicators
            if "philips hue bridge" in txt or "ipbridge" in txt or "hue bridge" in txt:
                return ip
            # sometimes header exposes it
            srv = r.headers.get("server", "")
            if "IpBridge" in srv or "ipbridge" in srv.lower():
                return ip
    except Exception:
        pass
    return None


def _discover_via_tcp_sweep(
    max_workers: int = 32, per_ip_timeout: float = 0.5
) -> list[str]:
    """Scan the local /24 via TCP GET to /description.xml in parallel."""
    prefixes = _primary_lan_prefixes()
    hits: set[str] = set()
    for p in prefixes:
        candidates = [f"{p}{i}" for i in range(1, 255)]
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futs = [
                ex.submit(_probe_bridge_ip, ip, per_ip_timeout) for ip in candidates
            ]
            for fut in as_completed(futs):
                ip = fut.result()
                if ip:
                    hits.add(ip)
    return sorted(hits)


def _app_config_dir() -> Path:
    """Returner en passende per-bruger configmappe på tværs af OS."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    d = base / "kivy-warmup"
    d.mkdir(parents=True, exist_ok=True)
    return d


CONFIG_PATH = _app_config_dir() / "hue_config.json"

_session = requests.Session()


def create_user(bridge_ip: str, devicetype: str = "kivy-warmup#pi") -> str:
    """
    Ask the bridge to create a username (press the LINK button first).
    Returns the new username string on success.
    Raises if link button not pressed (Hue error 101).
    """
    try:
        resp = _session.post(
            f"http://{bridge_ip}/api",
            json={"devicetype": devicetype},
            timeout=5,
        ).json()
        _raise_if_error(resp)
        # On success Hue returns: [{"success": {"username": "..."} }]
        if isinstance(resp, list):
            for item in resp:
                succ = item.get("success")
                if succ and "username" in succ:
                    return succ["username"]
        raise RuntimeError("Unexpected Hue response creating user.")
    except Exception as e:
        raise RuntimeError(f"Pairing failed: {e}")


def save_config(bridge_ip: str, username: str) -> None:
    """Persistér Hue bridge IP og username i CONFIG_PATH."""
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(
            json.dumps({"bridge_ip": bridge_ip, "username": username})
        )
    except Exception as e:
        raise RuntimeError(f"Kunne ikke gemme Hue-konfiguration: {e}")


def load_config() -> Dict[str, str]:
    """
    Hent Hue bridge IP og username.
    1) Fra fil i CONFIG_PATH, hvis den findes
    2) Ellers fra ENV variabler (HUE_BRIDGE_IP, HUE_USERNAME)
    3) Ellers tomme strenge
    """
    cfg = {"bridge_ip": "", "username": ""}
    try:
        if CONFIG_PATH.exists():
            cfg.update(json.loads(CONFIG_PATH.read_text()))
        else:
            cfg["bridge_ip"] = os.environ.get("HUE_BRIDGE_IP", "")
            cfg["username"] = os.environ.get("HUE_USERNAME", "")
    except Exception as e:
        # Hvis filen er korrupt: fallback til ENV
        cfg["bridge_ip"] = os.environ.get("HUE_BRIDGE_IP", "")
        cfg["username"] = os.environ.get("HUE_USERNAME", "")
    return cfg


def _raise_if_error(resp_json: Any) -> Any:
    """Raise RuntimeError if the Hue API returned an error."""
    # Common Hue responses are lists with { "success": ... } or { "error": ... }
    if isinstance(resp_json, list):
        for item in resp_json:
            if isinstance(item, dict) and "error" in item:
                err = item["error"]
                raise RuntimeError(
                    f"Hue error {err.get('type')}: {err.get('description')} ({err.get('address')})"
                )
        return resp_json

    # Some endpoints / firmwares may return a dict with "error"
    if isinstance(resp_json, dict) and "error" in resp_json:
        err = resp_json["error"]
        raise RuntimeError(
            f"Hue error {err.get('type')}: {err.get('description')} ({err.get('address')})"
        )

    return resp_json


def _api_url(*parts) -> str:
    """Construct a Hue API URL from a sequence of path parts."""
    cfg = load_config()
    return f"http://{cfg['bridge_ip']}/api/{cfg['username']}/{'/'.join(str(p) for p in parts)}"


def _api_url_from(bridge_ip: str, username: str, *parts) -> str:
    return f"http://{bridge_ip}/api/{username}/" + "/".join(str(p) for p in parts)


def list_lights_detailed() -> Dict[int, Dict[str, Any]]:
    """Return {id: {name, on, bri, supports_color}} for all lights."""
    data = _session.get(_api_url("lights"), timeout=4).json()
    _raise_if_error(data)
    out: Dict[int, Dict[str, Any]] = {}
    for lid, info in data.items():
        state = info.get("state", {})
        on = bool(state.get("on", False))
        bri_raw = state.get("bri", 254)
        bri = (
            int(round(bri_raw * 100 / 254))
            if isinstance(bri_raw, (int, float))
            else (100 if on else 0)
        )
        supports = ("hue" in state) or (state.get("colormode") in ("hs", "xy"))
        out[int(lid)] = {
            "name": info.get("name", f"Light {lid}"),
            "on": on,
            "bri": bri,
            "supports_color": supports,
        }
    return out


def list_lights_detailed_for_room(room_id: int) -> Dict[int, Dict[str, Any]]:
    """
    Returner {light_id: {...}} for lamper i et givent room (group).
    """
    # 1) Find hvilke light-ids der er i gruppen
    group = _session.get(_api_url("groups", room_id), timeout=4).json()
    _raise_if_error(group)
    light_ids = [int(x) for x in group.get("lights", [])]

    if not light_ids:
        return {}

    # 2) Hent alle lamper og filtrér
    all_lights = _session.get(_api_url("lights"), timeout=4).json()
    _raise_if_error(all_lights)

    out: Dict[int, Dict[str, Any]] = {}
    for lid in light_ids:
        info = all_lights.get(str(lid))
        if not info:
            continue
        state = info.get("state", {})
        on = bool(state.get("on", False))
        bri_raw = state.get("bri", 254)
        bri = (
            int(round(bri_raw * 100 / 254))
            if isinstance(bri_raw, (int, float))
            else (100 if on else 0)
        )
        supports = ("hue" in state) or (state.get("colormode") in ("hs", "xy"))
        out[int(lid)] = {
            "name": info.get("name", f"Light {lid}"),
            "on": on,
            "bri": bri,
            "supports_color": supports,
        }
    return out


def list_lights_detailed_for(
    bridge_ip: str, username: str
) -> Dict[int, Dict[str, Any]]:
    data = _session.get(_api_url_from(bridge_ip, username, "lights"), timeout=4).json()
    _raise_if_error(data)
    out: Dict[int, Dict[str, Any]] = {}
    for lid, info in data.items():
        state = info.get("state", {})
        on = bool(state.get("on", False))
        bri_raw = state.get("bri", 254)
        bri = (
            int(round(bri_raw * 100 / 254))
            if isinstance(bri_raw, (int, float))
            else (100 if on else 0)
        )
        supports = ("hue" in state) or (state.get("colormode") in ("hs", "xy"))
        out[int(lid)] = {
            "name": info.get("name", f"Light {lid}"),
            "on": on,
            "bri": bri,
            "supports_color": supports,
        }
    return out


def set_on(light_id: int, on: bool) -> Any:
    """Turn a light on or off."""
    data = _session.put(
        _api_url("lights", light_id, "state"), json={"on": bool(on)}, timeout=4
    ).json()
    return _raise_if_error(data)


def set_brightness(light_id: int, percent: int) -> Any:
    """Set brightness for a light (0–100%). Zero brightness is treated as off."""
    pct = max(0, min(100, int(percent)))
    bri = max(1, min(254, int(round(pct * 254 / 100))))
    data = _session.put(
        _api_url("lights", light_id, "state"), json={"on": True, "bri": bri}, timeout=4
    ).json()
    return _raise_if_error(data)


def set_color_hs(light_id: int, hue_degrees: float, sat_percent: float) -> Any:
    """Set hue (degrees 0–359) and saturation (0–100%) for a light."""
    hue = int(round((hue_degrees % 360) * 65535 / 360.0))
    sat = int(round(max(0, min(100, sat_percent)) * 254 / 100.0))
    data = _session.put(
        _api_url("lights", light_id, "state"),
        json={"on": True, "hue": hue, "sat": sat},
        timeout=4,
    ).json()
    return _raise_if_error(data)


def list_rooms_detailed() -> Dict[int, Dict[str, Any]]:
    data = _session.get(_api_url("groups"), timeout=4).json()
    _raise_if_error(data)
    out: Dict[int, Dict[str, Any]] = {}
    for gid, info in data.items():
        if info.get("type") != "Room":
            continue
        st = info.get("state", {})  # <-- use state.*
        act = info.get("action", {})
        on = bool(st.get("any_on", st.get("all_on", act.get("on", False))))
        bri_raw = act.get("bri", 254)  # bri still comes from action
        bri = (
            int(round(bri_raw * 100 / 254))
            if isinstance(bri_raw, (int, float))
            else (100 if on else 0)
        )
        supports = ("hue" in act) or (act.get("colormode") in ("hs", "xy"))
        out[int(gid)] = {
            "name": info.get("name", f"Room {gid}"),
            "on": on,
            "bri": bri,
            "supports_color": supports,
        }
    return out


def set_room_on(group_id: int, on: bool) -> Any:
    """Turn a room group on or off."""
    data = _session.put(
        _api_url("groups", group_id, "action"), json={"on": bool(on)}, timeout=4
    ).json()
    return _raise_if_error(data)


def set_room_brightness(group_id: int, percent: int) -> Any:
    """Set brightness for a room group (0–100%)."""
    pct = max(0, min(100, int(percent)))
    bri = max(1, min(254, int(round(pct * 254 / 100))))
    data = _session.put(
        _api_url("groups", group_id, "action"), json={"on": True, "bri": bri}, timeout=4
    ).json()
    return _raise_if_error(data)


def set_room_color_hs(group_id: int, hue_degrees: float, sat_percent: float) -> Any:
    """Set hue and saturation for a room group."""
    hue = int(round((hue_degrees % 360) * 65535 / 360.0))
    sat = int(round(max(0, min(100, sat_percent)) * 254 / 100.0))
    data = _session.put(
        _api_url("groups", group_id, "action"),
        json={"on": True, "hue": hue, "sat": sat},
        timeout=4,
    ).json()
    return _raise_if_error(data)


def discover_bridges(skip_cloud: bool = False) -> list[str]:
    ips: list[str] = []
    if not skip_cloud:
        try:
            ips.extend(_discover_via_meethue())
        except Exception:
            pass
    try:
        ips.extend(_discover_via_ssdp(timeout=1.5, tries=3))
    except Exception:
        pass
    try:
        mdns_ip = _resolve_mdns_default()
        if mdns_ip:
            ips.append(mdns_ip)
    except Exception:
        pass

    try:
        ips.extend(_discover_via_tcp_sweep())
    except Exception:
        pass
    return sorted(set(ips))


def _discover_via_meethue() -> List[str]:
    resp = _session.get("https://discovery.meethue.com", timeout=5)
    resp.raise_for_status()
    data = resp.json()
    ips: List[str] = []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                ip = item.get("internalipaddress")
                if ip:
                    ips.append(str(ip))
    return ips


def _discover_via_ssdp(timeout: float = 1.5, tries: int = 3) -> List[str]:
    """
    SSDP M-SEARCH for Hue bridges on the local network.
    We look for responses that mention IpBridge and parse LOCATION/USN headers.
    """
    MCAST_GRP = ("239.255.255.250", 1900)
    ST = "upnp:rootdevice"  # Broad; we filter for IpBridge in SERVER or hue-bridgeid
    msg = (
        "M-SEARCH * HTTP/1.1\r\n"
        f"HOST: {MCAST_GRP[0]}:{MCAST_GRP[1]}\r\n"
        'MAN: "ssdp:discover"\r\n'
        f"ST: {ST}\r\n"
        "MX: 1\r\n"
        "\r\n"
    ).encode("utf-8")

    found_ips: set[str] = set()
    for _ in range(max(1, tries)):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        s.settimeout(timeout)
        try:
            s.sendto(msg, MCAST_GRP)
            while True:
                try:
                    data, addr = s.recvfrom(65535)
                except socket.timeout:
                    break
                ip = addr[0]
                text = data.decode("utf-8", errors="ignore")
                # Heuristic: Philips bridges typically advertise "IpBridge" and hue-bridgeid
                if ("IpBridge" in text) or ("hue-bridgeid" in text.lower()):
                    found_ips.add(ip)
                    # If LOCATION header exists, we could confirm via device description
                    # but it requires an extra GET; the heuristic is usually enough.
        finally:
            s.close()
    return list(found_ips)


def _resolve_mdns_default() -> str | None:
    """
    Try to resolve the default mDNS hostname most Hue bridges expose.
    Not guaranteed to exist on all networks.
    """
    try:
        infos = socket.getaddrinfo("philips-hue.local", None)
        for family, _, _, _, sockaddr in infos:
            if family == socket.AF_INET:
                return sockaddr[0]
    except Exception:
        return None
    return None


def light_is_on(light_id: int) -> bool:
    data = _session.get(_api_url("lights", light_id), timeout=4).json()
    _raise_if_error(data)
    return bool(data.get("state", {}).get("on", False))


def room_is_on(group_id: int) -> bool:
    # Prefer 'state' -> any_on/all_on when available, else fall back to action.on
    data = _session.get(_api_url("groups", group_id), timeout=4).json()
    _raise_if_error(data)
    st = data.get("state", {})
    if "any_on" in st or "all_on" in st:
        return bool(st.get("any_on") or st.get("all_on"))
    return bool(data.get("action", {}).get("on", False))
