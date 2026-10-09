"""Mass models (P1 §4.5), shared by L6 and the exporter. Pure Python 3.11.

Printed part: m = rho * (V_shell + f_infill * (V - V_shell)), V_shell = min(V, A * t_wall),
t_wall = wall_count * line_width (profile). Component: card mass_kg at its envelope centre (plan level)
or at its model's centre of volume (assembly level).
"""
from components.card import resolve
from profiles import load_materials


def printed_mass_kg(volume_mm3, area_mm2, material, profile):
    rho = load_materials()[material]["density_g_cm3"] * 1e-6       # kg / mm^3
    t = profile.get("wall_count", 3) * profile.get("line_width_mm", 0.45)
    shell = min(volume_mm3, area_mm2 * t)
    return rho * (shell + profile.get("infill", 0.2) * (volume_mm3 - shell))


def card_mass_kg(card):
    return float(card.get("mass_kg") or 0.0)


def card_com_mm(card):
    """Card-frame centre of its envelope: interfaces.volume, else mount_pocket, else the frame origin."""
    itf = card.get("interfaces") or {}
    if itf.get("volume"):
        v = resolve(card, itf["volume"])
        return [v["centre_xy"][0], v["centre_xy"][1], v.get("bottom_z", 0.0) + v["size"][2] / 2]
    if itf.get("mount_pocket"):
        v = resolve(card, itf["mount_pocket"])
        return [v["centre_xy"][0], v["centre_xy"][1], v.get("top_z", 0.0) - v["size"][2] / 2]
    return [0.0, 0.0, 0.0]


def actuator_limit(card, servos_on_axis=1):
    """(allowed holding torque N.m, stall N.m at 6 V, derating) for a servo card, or None."""
    act = card.get("actuation") or {}
    stall = (act.get("stall_torque_Nm") or {}).get("at_6V")
    if stall is None:
        return None
    derating = act.get("derating", 0.5)
    return stall * derating * servos_on_axis, stall, derating
