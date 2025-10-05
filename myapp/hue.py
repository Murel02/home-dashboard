"""
Hue API wrapper for controlling Philips Hue lights and rooms.
Works with Hue v1 API on the local bridge.
"""

from __future__ import annotations
import json
import os
import socket
import sys
from pathlib import Path
from typing import Any, Dict, List

import requests

# ---------------------------
# Config path & HTTP session
# ---------------------------


def _app_config_dir() -> Path:
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
_session.headers.update({"Accept": "application/json"})

# ---------------------------
# Helpers
# ---------------------------


def save_config(bridge_ip: str, username: str) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps({"bridge_ip": bridge_ip, "username": username}))


def load_config() -> Dict[str, str]:
    cfg = {"bridge_ip": "", "username": ""}
    try:
        if CONFIG_PATH.exists():
            cfg.update(json.loads(CONFIG_PATH.read_text()))
        else:
            cfg["bridge_ip"] = os.environ.get("HUE_BRIDGE_IP", "")
            cfg["username"] = os.environ.get("HUE_USERNAME", "")
    except Exception:
        cfg["bridge_ip"] = os.environ.get("HUE_BRIDGE_IP", "")
        cfg["username"] = os.environ.get("HUE_USERNAME", "")
    return cfg


def _raise_if_error(resp_json: Any) -> Any:
    # Hue often returns a list of { "success": {...} } or { "error": {...} }
    if isinstance(resp_json, list):
        for item in resp_json:
            if isinstance(item, dict) and "error" in item:
                err = item["error"]
                raise RuntimeError(
                    f"Hue error {err.get('type')}: {err.get('description')} ({err.get('address')})"
                )
        return resp_json
    if isinstance(resp_json, dict) and "error" in resp_json:
        err = resp_json["error"]
        raise RuntimeError(
            f"Hue error {err.get('type')}: {err.get('description')} ({err.get('address')})"
        )
    return resp_json


def _api_url(*parts) -> str:
    cfg = load_config()
    return f"http://{cfg['bridge_ip']}/api/{cfg['username']}/" + "/".join(
        str(p) for p in parts
    )


# ---------------------------
# Pairing
# ---------------------------


def create_user(bridge_ip: str, devicetype: str = "kivy-warmup#pi") -> str:
    resp = _session.post(
        f"http://{bridge_ip}/api",
        json={"devicetype": devicetype},
        timeout=5,
    ).json()
    _raise_if_error(resp)
    if isinstance(resp, list):
        for item in resp:
            succ = item.get("success")
            if succ and "username" in succ:
                return succ["username"]
    raise RuntimeError("Unexpected Hue response creating user.")


# ---------------------------
# Lights (per light)
# ---------------------------


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


def light_is_on(light_id: int) -> bool:
    """True if that specific light is currently on."""
    data = _session.get(_api_url("lights", light_id), timeout=4).json()
    _raise_if_error(data)
    return bool(data.get("state", {}).get("on", False))


def set_on(light_id: int, on: bool) -> Any:
    data = _session.put(
        _api_url("lights", light_id, "state"), json={"on": bool(on)}, timeout=4
    ).json()
    return _raise_if_error(data)


def set_brightness(light_id: int, percent: int) -> Any:
    pct = max(0, min(100, int(percent)))
    if pct <= 0:
        # Turning brightness to 0 → off
        return set_on(light_id, False)
    bri = max(1, min(254, int(round(pct * 254 / 100))))
    data = _session.put(
        _api_url("lights", light_id, "state"), json={"on": True, "bri": bri}, timeout=4
    ).json()
    return _raise_if_error(data)


def set_color_hs(light_id: int, hue_degrees: float, sat_percent: float) -> Any:
    hue = int(round((hue_degrees % 360) * 65535 / 360.0))
    sat = int(round(max(0, min(100, sat_percent)) * 254 / 100.0))
    data = _session.put(
        _api_url("lights", light_id, "state"),
        json={"on": True, "hue": hue, "sat": sat},
        timeout=4,
    ).json()
    return _raise_if_error(data)


# ---------------------------
# Rooms (Hue groups of type "Room")
# ---------------------------


def list_rooms_detailed() -> Dict[int, Dict[str, Any]]:
    """
    Return {group_id: {name, on, bri, supports_color}} for all Room groups.
    For 'on', prefer group's state.any_on when available.
    """
    groups = _session.get(_api_url("groups"), timeout=4).json()
    _raise_if_error(groups)
    out: Dict[int, Dict[str, Any]] = {}
    for gid, info in groups.items():
        if info.get("type") != "Room":
            continue
        # Prefer 'state.any_on' (actual live state). Fallback to 'action.on'.
        st = info.get("state", {})
        act = info.get("action", {})
        on = bool(st.get("any_on", act.get("on", False)))
        bri_raw = act.get("bri", 254)
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


def room_is_on(group_id: int) -> bool:
    """
    True if any light in the room is on.
    Uses groups/<id>.state.any_on when present (Hue's reliable indicator).
    """
    data = _session.get(_api_url("groups", group_id), timeout=4).json()
    _raise_if_error(data)
    st = data.get("state", {})
    if "any_on" in st:
        return bool(st["any_on"])
    # Fallback if state is missing (older firmware): use action.on
    return bool(data.get("action", {}).get("on", False))


def list_lights_detailed_for_room(room_id: int) -> Dict[int, Dict[str, Any]]:
    """
    {light_id: {...}} for lights that belong to a given room group.
    """
    group = _session.get(_api_url("groups", room_id), timeout=4).json()
    _raise_if_error(group)
    light_ids = [int(x) for x in group.get("lights", [])]
    if not light_ids:
        return {}

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


def set_room_on(group_id: int, on: bool) -> Any:
    data = _session.put(
        _api_url("groups", group_id, "action"), json={"on": bool(on)}, timeout=4
    ).json()
    return _raise_if_error(data)


def set_room_brightness(group_id: int, percent: int) -> Any:
    pct = max(0, min(100, int(percent)))
    if pct <= 0:
        return set_room_on(group_id, False)
    bri = max(1, min(254, int(round(pct * 254 / 100))))
    data = _session.put(
        _api_url("groups", group_id, "action"), json={"on": True, "bri": bri}, timeout=4
    ).json()
    return _raise_if_error(data)


def set_room_color_hs(group_id: int, hue_degrees: float, sat_percent: float) -> Any:
    hue = int(round((hue_degrees % 360) * 65535 / 360.0))
    sat = int(round(max(0, min(100, sat_percent)) * 254 / 100.0))
    data = _session.put(
        _api_url("groups", group_id, "action"),
        json={"on": True, "hue": hue, "sat": sat},
        timeout=4,
    ).json()
    return _raise_if_error(data)


# ---------------------------
# Discovery (optional helpers)
# ---------------------------


def _primary_lan_prefixes() -> list[str]:
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
    if not prefixes:
        prefixes = ["192.168.1.", "192.168.0.", "10.0.0."]
    return prefixes


def discover_bridges(skip_cloud: bool = False) -> list[str]:
    # Dumb-local sweep fallback; decent on small LANs
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def probe(ip: str, timeout: float = 0.5) -> str | None:
        try:
            r = _session.get(f"http://{ip}/description.xml", timeout=timeout)
            if r.ok:
                txt = r.text.lower()
                if "philip" in txt and "bridge" in txt:
                    return ip
                if "ipbridge" in r.headers.get("server", "").lower():
                    return ip
        except Exception:
            pass
        return None

    hits: set[str] = set()
    for p in _primary_lan_prefixes():
        candidates = [f"{p}{i}" for i in range(1, 255)]
        with ThreadPoolExecutor(max_workers=32) as ex:
            futs = [ex.submit(probe, ip) for ip in candidates]
            for fut in as_completed(futs):
                ip = fut.result()
                if ip:
                    hits.add(ip)
    return sorted(hits)
