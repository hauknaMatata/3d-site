"""Offline procedural planner — works without any LLM API key.

It is NOT an AI model: it recognises a set of object families and edit commands (PL/EN) and turns
them into a sequence of real engine operations with user parameters, so the model stays editable.
With an LLM provider configured, the LLM agent drives the same tools for arbitrary objects."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import nlp

CLAY = {"preset": "plastic", "color": "#a4aab1"}


@dataclass
class Plan:
    kind: str  # create | edit | unknown
    title: str
    steps: list[dict[str, Any]] = field(default_factory=list)
    params: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def S(_stage: str, _tool: str, _label: str, **args) -> dict:
    return {"stage": _stage, "tool": _tool, "label": _label, "args": args}


# ============================================================================ CAD
def cad_enclosure(prompt: str, att: list[dict]) -> Plan:
    d = nlp.dims3(prompt) or (100.0, 60.0, 40.0)
    wall = nlp.mm_after(prompt, r"(?:ścian|scian|wall|grubo)") or 2.0
    holes = nlp.count_of(prompt, r"(?:otwor|otwór|holes?|mounting)") or (4 if nlp.has(prompt, "otw", "hole") else 0)
    hole_d = nlp.mm_after(prompt, r"(?:średnic|srednic|diameter|ø|fi)") or 3.4
    m3 = nlp.norm(prompt).find("m3") >= 0
    if m3:
        hole_d = 3.4
    color = nlp.color_near(prompt, ("obudow", "enclosure", "case", "korpus", "kolor")) or "#6b7480"
    want_logo = nlp.has(prompt, "logo", "grafik", "obraz", "image", "graphic", "załącz", "zalacz", "emblemat")
    engrave = nlp.has(prompt, "grawer", "engrav", "wytłocz", "wklęs")
    depth = nlp.mm_after(prompt, r"(?:głębokoś|glebokos|depth)") or (0.5 if engrave else 0.6)
    p = Plan("create", "Obudowa (enclosure)", params={
        "W": d[0], "D": d[1], "H": d[2], "wall": wall, "lid": wall, "corner_r": min(4.0, wall * 2),
        "boss_r": max(3.0, hole_d), "hole_d": hole_d, "pilot_d": round(max(1.6, hole_d * 0.75), 2),
        "inset": round(wall + max(3.0, hole_d) - 1.0, 2), "color": color, "logo_w": round(min(d[0], d[1]) * 0.5, 1),
        "logo_depth": depth})
    st = "Główna bryła"
    p.steps += [
        S(st, "create_box", "Korpus W×D×(H−lid)", name="Base", width="=W", depth="=D", height="=H-lid", color="=color"),
        S(st, "fillet", "Zaokrąglenie pionowych krawędzi", target="Base", radius="=corner_r", edges="|Z"),
        S("Skorupa (shell)", "shell", "Ścianki = wall, otwarta góra", target="Base", thickness="=wall", open_face=">Z"),
    ]
    if holes:
        corners = [(-1, -1), (1, -1), (1, 1), (-1, 1)][:holes] if holes <= 4 else None
        pts = corners or [(-1, -1), (1, -1), (1, 1), (-1, 1)]
        for i, (sx, sy) in enumerate(pts):
            p.steps.append(S("Słupki montażowe", "create_cylinder", f"Słupek {i + 1}", operation="add", target="Base",
                             radius="=boss_r", height="=H-lid-wall",
                             position=[f"={sx}*(W/2-inset)", f"={sy}*(D/2-inset)", "=wall"]))
        for i, (sx, sy) in enumerate(pts):
            p.steps.append(S("Otwory montażowe", "create_cylinder", f"Otwór pilotowy {i + 1}", operation="cut", target="Base",
                             radius="=pilot_d/2", height="=H-lid-wall-1",
                             position=[f"={sx}*(W/2-inset)", f"={sy}*(D/2-inset)", "=wall+1"]))
    p.steps += [
        S("Pokrywa", "create_box", "Pokrywa", name="Lid", width="=W", depth="=D", height="=lid", position=[0, 0, "=H-lid"],
          color="=color"),
        S("Pokrywa", "fillet", "Zaokrąglenie pokrywy", target="Lid", radius="=corner_r", edges="|Z"),
    ]
    if holes:
        p.steps.append(S("Otwory montażowe", "hole", f"{holes} otwory przelotowe z fazą", target="Lid", face=">Z",
                         diameter="=hole_d", countersink_diameter="=hole_d*1.9",
                         positions=[[f"={sx}*(W/2-inset)", f"={sy}*(D/2-inset)"] for sx, sy in pts]))
    if want_logo:
        img = next((a for a in att if a["mime"].startswith("image/")), None)
        if img:
            p.steps.append(S("Logo", "apply_image", "Logo z załącznika na górnej powierzchni", target="Lid",
                             attachment_id=img["id"], face=">Z", mode="engrave" if engrave else "emboss",
                             depth="=logo_depth", width="=logo_w", color=img.get("palette", ["#1f6feb"])[0]
                             if not engrave else None))
        else:
            p.steps.append(S("Logo", "add_text", "Tekst LOGO (brak załączonej grafiki)", target="Lid", text="LOGO",
                             face=">Z", size="=logo_w/4", depth="=logo_depth", mode="engrave" if engrave else "emboss",
                             color="#1f6feb"))
            p.notes.append("Nie załączono grafiki — dodałem tekst „LOGO”. Załącz PNG/SVG i napisz „użyj logo z załącznika”.")
    p.steps.append(S("Kontrola wymiarów", "measure", "Pomiar korpusu", target="Base"))
    p.steps.append(S("Kontrola wymiarów", "measure", "Pomiar pokrywy", target="Lid"))
    return p


def cad_plate(prompt, att):
    d = nlp.dims3(prompt)
    if d:
        w, dd, t = d
    else:
        w, dd = nlp.dims2(prompt) or (80.0, 50.0)
        t = nlp.mm_after(prompt, r"(?:grubo|thick)") or 4.0
    holes = nlp.count_of(prompt, r"(?:otwor|otwór|holes?)") or (4 if nlp.has(prompt, "otw", "hole") else 0)
    p = Plan("create", "Płyta montażowa", params={"W": w, "D": dd, "T": t, "hole_d": nlp.mm_after(prompt, r"(?:średnic|srednic|diameter|ø)") or 4.5,
                                                   "inset": 6.0, "color": nlp.first_color(prompt) or "#4a5160"})
    p.steps += [S("Główna bryła", "create_box", "Płyta", name="Plate", width="=W", depth="=D", height="=T", color="=color"),
                S("Krawędzie", "fillet", "Zaokrąglenie narożników", target="Plate", radius=3, edges="|Z")]
    if holes:
        p.steps.append(S("Otwory", "hole", f"{holes} otwory narożne", target="Plate", face=">Z", diameter="=hole_d",
                         positions=[[f"={sx}*(W/2-inset)", f"={sy}*(D/2-inset)"] for sx, sy in [(-1, -1), (1, -1), (1, 1), (-1, 1)][:holes]]))
    p.steps.append(S("Krawędzie", "chamfer", "Faza górnych krawędzi", target="Plate", distance=0.6, edges=">Z"))
    _cad_logo(p, prompt, att, "Plate")
    p.steps.append(S("Kontrola wymiarów", "measure", "Pomiar", target="Plate"))
    return p


def _cad_logo(p: Plan, prompt, att, target):
    if nlp.has(prompt, "logo", "grafik", "obraz", "image", "graphic"):
        img = next((a for a in att if a["mime"].startswith("image/")), None)
        if img:
            eng = nlp.has(prompt, "grawer", "engrav")
            p.steps.append(S("Logo", "apply_image", "Logo z załącznika", target=target, attachment_id=img["id"], face=">Z",
                             mode="engrave" if eng else "emboss",
                             depth=nlp.mm_after(prompt, r"(?:głębokoś|glebokos|depth)") or (0.5 if eng else 0.6)))


def cad_tube(prompt, att):
    od = nlp.mm_after(prompt, r"(?:zewn|outer|od\b|średnic|srednic|diameter)") or 30.0
    idd = nlp.mm_after(prompt, r"(?:wewn|inner|id\b|otw)") or od * 0.6
    ln = nlp.mm_after(prompt, r"(?:dług|dlug|wysok|length|height)") or 40.0
    p = Plan("create", "Tuleja / dystans", params={"OD": od, "ID": idd, "L": ln, "color": nlp.first_color(prompt) or "#8a9099"})
    p.steps += [S("Główna bryła", "create_cylinder", "Cylinder zewnętrzny", name="Tube", radius="=OD/2", height="=L", color="=color"),
                S("Otwór", "create_cylinder", "Otwór wewnętrzny", operation="cut", target="Tube", radius="=ID/2", height="=L+2", position=[0, 0, -1]),
                S("Krawędzie", "chamfer", "Fazy", target="Tube", distance=0.8, edges="%CIRCLE"),
                S("Kontrola wymiarów", "measure", "Pomiar", target="Tube")]
    return p


def cad_bracket(prompt, att):
    w = nlp.mm_after(prompt, r"(?:szerok|width)") or 40.0
    a = nlp.mm_after(prompt, r"(?:ramię|ramie|leg|arm|dług|dlug)") or 50.0
    t = nlp.mm_after(prompt, r"(?:grubo|thick)") or 4.0
    p = Plan("create", "Uchwyt kątowy L", params={"Wd": w, "A": a, "T": t, "hole_d": 5.0, "color": nlp.first_color(prompt) or "#3f6fb5"})
    p.steps += [
        S("Szkic profilu", "extrude_sketch", "Profil L wyciągnięty", name="Bracket", plane="XZ",
          sketch={"type": "polygon", "points": [[0, 0], ["=A", 0], ["=A", "=T"], ["=T", "=T"], ["=T", "=A"], [0, "=A"]]},
          distance="=Wd", color="=color"),
        S("Wzmocnienie", "extrude_sketch", "Żebro", operation="add", target="Bracket", plane="XZ", plane_offset="=-Wd/2+T/2",
          sketch={"type": "polygon", "points": [["=T", "=T"], ["=A*0.6", "=T"], ["=T", "=A*0.6"]]}, distance="=T"),
        S("Otwory", "hole", "Otwory w podstawie", target="Bracket", face="<Z", diameter="=hole_d",
          positions=[["=A*0.6", "=-Wd/4"], ["=A*0.6", "=Wd/4"]]),
        S("Krawędzie", "fillet", "Zaokrąglenie wewnętrzne", target="Bracket", radius=1.0, edges="|Y"),
        S("Kontrola wymiarów", "measure", "Pomiar", target="Bracket"),
    ]
    return p


def cad_box_generic(prompt, att):
    d = nlp.dims3(prompt) or (50.0, 50.0, 20.0)
    p = Plan("create", "Bryła prostopadłościenna", params={"W": d[0], "D": d[1], "H": d[2], "color": nlp.first_color(prompt) or "#8a9099"})
    p.steps.append(S("Główna bryła", "create_box", "Bryła", name="Body", width="=W", depth="=D", height="=H", color="=color"))
    if nlp.has(prompt, "zaokrągl", "zaokragl", "fillet", "round"):
        p.steps.append(S("Krawędzie", "fillet", "Zaokrąglenie", target="Body", radius=nlp.mm_after(prompt, r"(?:promie|radius|r\b)") or 2, edges="|Z"))
    _cad_logo(p, prompt, att, "Body")
    p.steps.append(S("Kontrola wymiarów", "measure", "Pomiar", target="Body"))
    return p


# ============================================================================ GAME
def game_car(prompt: str, att: list[dict]) -> Plan:
    paint = nlp.color_near(prompt, ("lakier", "karoser", "paint", "body", "kolor", "samoch", "car", "auto")) or "#b3121b"
    rim = nlp.color_near(prompt, ("feln", "felg", "rim", "wheel", "obręcz")) or "#1a1a1c"
    if paint == rim:  # "czarne felgi, czerwony lakier" – resolve by nearest keyword only
        paint = nlp.color_near(prompt, ("lakier", "karoser", "paint"), 25) or "#b3121b"
    interior = nlp.color_near(prompt, ("wnętrz", "wnetrz", "interior", "fotel", "seat"), 30) or "#2a1d18"
    p = Plan("create", "Futurystyczny samochód sportowy", params={
        "paint": paint, "rim": rim, "interior": interior, "caliper": "#d01818", "accent": "#16181c",
        "wheel_r": 0.35, "track": 0.82})
    G = "Główna geometria"
    body = [(-2.30, 1.62, 0.36, 0.66, 2.3, 5), (-2.22, 1.86, 0.54, 0.64, 2.5, 6), (-1.90, 1.98, 0.64, 0.62, 2.6, 6),
            (-1.30, 2.00, 0.66, 0.61, 2.6, 6), (-0.60, 1.94, 0.62, 0.60, 2.6, 6), (0.20, 1.92, 0.58, 0.58, 2.6, 6),
            (1.00, 1.94, 0.54, 0.56, 2.5, 6), (1.60, 1.90, 0.46, 0.52, 2.4, 6), (2.05, 1.78, 0.34, 0.47, 2.3, 5),
            (2.30, 1.50, 0.20, 0.43, 2.2, 4)]
    p.steps.append(S(G, "add_loft", "Bryła nadwozia (loft 10 przekrojów)", name="Body", segments=64, smoothness=5,
                     sections=[dict(x=x, width=w, height=h, y=y, exp_top=et, exp_bottom=eb) for x, w, h, y, et, eb in body],
                     material=CLAY))
    K = "Karoseria"
    for i, (x, zs) in enumerate([(1.40, 1), (1.40, -1), (-1.35, 1), (-1.35, -1)]):
        p.steps.append(S(K, "add_primitive", f"Wycinak nadkola {i + 1}", name=f"ArchCut{i + 1}", shape="cylinder", radius=0.415,
                         height=0.42, position=[x, 0.35, zs * 0.84], rotation=[90, 0, 0], segments=48))
        p.steps.append(S(K, "boolean", f"Nadkole {i + 1} (boolean)", target="Body", tool=f"ArchCut{i + 1}", operation="difference"))
    p.steps += [
        S(K, "add_primitive", "Wycinak kokpitu", name="CockpitCut", shape="rounded_box", size=[1.75, 0.62, 1.56], radius=0.12,
          position=[-0.28, 0.85, 0]),
        S(K, "boolean", "Kokpit (boolean)", target="Body", tool="CockpitCut", operation="difference"),
        S(K, "add_primitive", "Wlot przedni", name="FrontIntake", shape="rounded_box", size=[0.12, 0.15, 1.15], radius=0.04,
          position=[2.2, 0.38, 0], material=CLAY),
        S(K, "add_primitive", "Dyfuzor", name="Diffuser", shape="rounded_box", size=[0.55, 0.12, 1.45], radius=0.03,
          position=[-2.08, 0.33, 0], material=CLAY),
        S(K, "add_primitive", "Spoiler — skrzydło", name="Wing", shape="rounded_box", size=[0.34, 0.035, 1.78], radius=0.015,
          position=[-2.02, 1.13, 0], rotation=[0, 0, -6], material=CLAY),
    ]
    for zs in (1, -1):
        sfx = "L" if zs > 0 else "R"
        p.steps += [
            S(K, "add_primitive", f"Mocowanie spoilera {sfx}", name=f"WingStrut{sfx}", shape="rounded_box", size=[0.07, 0.24, 0.03],
              radius=0.01, position=[-1.98, 0.99, zs * 0.5], material=CLAY),
            S(K, "add_primitive", f"Wlot boczny {sfx}", name=f"SideIntake{sfx}", shape="rounded_box", size=[0.55, 0.17, 0.08],
              radius=0.04, position=[-0.78, 0.62, zs * 0.975], material=CLAY),
            S(K, "add_primitive", f"Lusterko {sfx}", name=f"Mirror{sfx}", shape="rounded_box", size=[0.17, 0.09, 0.13], radius=0.035,
              position=[0.48, 1.0, zs * 1.0], material=CLAY),
            S(K, "add_primitive", f"Próg {sfx}", name=f"SideSkirt{sfx}", shape="rounded_box", size=[2.1, 0.07, 0.07], radius=0.02,
              position=[0.02, 0.31, zs * 0.95], material=CLAY),
        ]
    W = "Koła"
    for name, x, zs, wd in (("WheelFL", 1.40, 1, 0.27), ("WheelFR", 1.40, -1, 0.27), ("WheelRL", -1.35, 1, 0.3), ("WheelRR", -1.35, -1, 0.3)):
        p.steps.append(S(W, "add_wheel", f"Koło {name[-2:]} (opona, felga 10 ramion, tarcza, zacisk)", name=name, radius="=wheel_r",
                         width=wd, spokes=10, side=zs, position=[x, "=wheel_r", f"={zs}*track"],
                         rim_material={"preset": "metal_painted", "color": "=rim"}, caliper_color="=caliper"))
    Sz = "Szyby"
    cabin = [(-1.38, 1.24, 0.05, 0.90), (-1.08, 1.36, 0.30, 0.985), (-0.58, 1.42, 0.42, 1.0), (0.02, 1.40, 0.40, 0.975),
             (0.52, 1.34, 0.25, 0.915), (0.95, 1.18, 0.05, 0.86)]
    p.steps += [
        S(Sz, "add_loft", "Kabina — szkło przyciemniane", name="Glass", segments=56, smoothness=5,
          sections=[dict(x=x, width=w, height=h, y=y, exp_top=2.3, exp_bottom=6) for x, w, h, y in cabin],
          material={"preset": "glass", "color": "#2a3540", "opacity": 0.38}),
        S(Sz, "add_loft", "Dach", name="Roof", segments=56, smoothness=4,
          sections=[dict(x=-0.95, width=1.18, height=0.03, y=1.175, exp_top=2.2, exp_bottom=2.2),
                    dict(x=-0.55, width=1.3, height=0.05, y=1.205, exp_top=2.2, exp_bottom=2.2),
                    dict(x=0.0, width=1.28, height=0.05, y=1.19, exp_top=2.2, exp_bottom=2.2),
                    dict(x=0.32, width=1.18, height=0.03, y=1.155, exp_top=2.2, exp_bottom=2.2)], material=CLAY),
    ]
    I = "Wnętrze"
    p.steps += [
        S(I, "add_primitive", "Podłoga kabiny", name="CabinFloor", shape="box", size=[1.7, 0.04, 1.5], position=[-0.28, 0.56, 0],
          material={"preset": "fabric", "color": "#1d1f22"}),
        S(I, "add_primitive", "Deska rozdzielcza", name="Dashboard", shape="rounded_box", size=[0.34, 0.16, 1.48], radius=0.05,
          position=[0.42, 0.88, 0], material={"preset": "leather", "color": "=interior"}),
        S(I, "add_primitive", "Tunel środkowy", name="Console", shape="rounded_box", size=[0.95, 0.22, 0.22], radius=0.04,
          position=[-0.1, 0.66, 0], material={"preset": "carbon_fiber"}),
        S(I, "add_primitive", "Ekran", name="Screen", shape="rounded_box", size=[0.02, 0.1, 0.22], radius=0.008,
          position=[0.27, 0.95, 0], rotation=[0, 0, 15], material={"preset": "emissive", "color": "#4fc3ff", "emissive": 0.8}),
    ]
    for zs in (1, -1):
        sfx = "L" if zs > 0 else "R"
        p.steps += [
            S(I, "add_primitive", f"Fotel — siedzisko {sfx}", name=f"SeatBase{sfx}", shape="rounded_box", size=[0.5, 0.12, 0.5],
              radius=0.05, position=[-0.45, 0.64, zs * 0.38], material={"preset": "leather", "color": "=interior"}),
            S(I, "add_primitive", f"Fotel — oparcie {sfx}", name=f"SeatBack{sfx}", shape="rounded_box", size=[0.13, 0.58, 0.5],
              radius=0.06, position=[-0.74, 0.92, zs * 0.38], rotation=[0, 0, 14], material={"preset": "leather", "color": "=interior"}),
        ]
    p.steps += [
        S(I, "add_primitive", "Kierownica", name="SteeringWheel", shape="torus", radius=0.165, tube_radius=0.022,
          position=[0.2, 0.9, 0.38], rotation=[0, 0, 68], material={"preset": "leather", "color": "#151515"}),
        S(I, "add_tube", "Kolumna kierownicy", name="SteeringColumn", radius=0.025, path=[[0.2, 0.9, 0.38], [0.42, 0.84, 0.38]],
          material={"preset": "metal_brushed", "color": "#3a3d42"}),
    ]
    L = "Oświetlenie i detale"
    for zs in (1, -1):
        sfx = "L" if zs > 0 else "R"
        p.steps += [
            S(L, "add_primitive", f"Reflektor {sfx}", name=f"Headlight{sfx}", shape="rounded_box", size=[0.22, 0.035, 0.36],
              radius=0.015, position=[1.98, 0.645, zs * 0.6], rotation=[0, zs * 18, -16],
              material={"preset": "emissive", "color": "#dcecff", "emissive": 0.9}),
            S(L, "add_tube", f"Wydech {sfx}", name=f"Exhaust{sfx}", radius=0.045, path=[[-2.15, 0.37, zs * 0.33], [-2.31, 0.37, zs * 0.33]],
              segments=20, material={"preset": "chrome"}),
        ]
    p.steps.append(S(L, "add_primitive", "Pas świateł tylnych", name="Taillight", shape="rounded_box", size=[0.05, 0.045, 1.5],
                     radius=0.018, position=[-2.255, 0.79, 0], material={"preset": "emissive", "color": "#ff1428", "emissive": 1.0}))
    M = "Nakładanie materiałów"
    p.steps += [
        S(M, "set_material", "Lakier nadwozia (car paint, flakes)", target="Body", material={"preset": "car_paint", "color": "=paint"}),
        S(M, "set_material", "Lakier dachu", target="Roof", material={"preset": "car_paint", "color": "=paint"}),
        S(M, "set_material", "Lusterko L", target="MirrorL", material={"preset": "car_paint", "color": "=paint"}),
        S(M, "set_material", "Lusterko R", target="MirrorR", material={"preset": "car_paint", "color": "=paint"}),
        S(M, "set_material", "Carbon — skrzydło", target="Wing", material={"preset": "carbon_fiber"}),
        S(M, "set_material", "Carbon — dyfuzor", target="Diffuser", material={"preset": "carbon_fiber"}),
        S(M, "set_material", "Wlot przedni", target="FrontIntake", material={"preset": "plastic", "color": "=accent", "roughness": 0.6}),
    ]
    for sfx in ("L", "R"):
        p.steps += [S(M, "set_material", f"Carbon — progi {sfx}", target=f"SideSkirt{sfx}", material={"preset": "carbon_fiber"}),
                    S(M, "set_material", f"Wlot boczny {sfx}", target=f"SideIntake{sfx}", material={"preset": "plastic", "color": "=accent"}),
                    S(M, "set_material", f"Mocowanie {sfx}", target=f"WingStrut{sfx}", material={"preset": "carbon_fiber"})]
    img = next((a for a in att if a["mime"].startswith("image/")), None)
    if img and nlp.has(prompt, "logo", "grafik", "naklej", "decal", "drzw", "door"):
        for side in ("+z", "-z"):
            p.steps.append(S(M, "apply_decal", f"Logo na drzwiach ({side})", target="Body", attachment_id=img["id"], side=side,
                             center=[0.5, 0.55], width=0.5))
    p.steps.append(S("Generowanie tekstur", "set_texture_resolution", "Mapy PBR 1024 px (albedo, normal, ORM)", resolution=1024))
    p.steps.append(S("Końcowa optymalizacja", "generate_lods", "LOD1 (50%) i LOD2 (25%)", ratios=[0.5, 0.25]))
    p.steps.append(S("Końcowa optymalizacja", "scene_stats", "Statystyki sceny"))
    return p


def game_character(prompt, att):
    colors = {}
    img = next((a for a in att if a["mime"].startswith("image/")), None)
    if img and img.get("palette"):
        pal = img["palette"]
        def skinlike(h):
            r, g, b = (int(h[i:i + 2], 16) for i in (1, 3, 5))
            return r > 120 and r > g > b and r - b > 40
        skin = next((c for c in pal if skinlike(c)), None)
        rest = [c for c in pal if c != skin]
        if skin:
            colors["skin"] = skin
        if rest:
            colors["shirt"] = rest[0]
        if len(rest) > 1:
            colors["pants"] = rest[1]
        if len(rest) > 2:
            colors["hair"] = rest[2]
    for key, kws in (("shirt", ("koszul", "bluz", "kurtk", "shirt", "jacket", "top")), ("pants", ("spodn", "pants", "jeans")),
                     ("hair", ("włos", "wlos", "hair")), ("shoes", ("but", "shoe")), ("skin", ("skór", "skor", "skin"))):
        c = nlp.color_near(prompt, kws, 25)
        if c:
            colors[key] = c
    p = Plan("create", "Postać (stylizowana)", params={f"c_{k}": v for k, v in colors.items()})
    p.steps.append(S("Proporcje i bryła", "add_humanoid", "Postać — głowa, tułów, kończyny (8 głów wysokości)", name="Character",
                     height=1.8, colors={k: f"=c_{k}" for k in colors}))
    if nlp.has(prompt, "plecak", "backpack"):
        p.steps.append(S("Akcesoria", "add_primitive", "Plecak", name="Backpack", shape="rounded_box", size=[0.32, 0.42, 0.18],
                         radius=0.05, position=[0, 1.15, -0.2], parent="Character", material={"preset": "fabric", "color": "#4a5a3a"}))
    if nlp.has(prompt, "miecz", "sword"):
        p.steps += game_sword(prompt, att).steps
    p.steps.append(S("Generowanie tekstur", "set_texture_resolution", "Tekstury 1024 px", resolution=1024))
    p.steps.append(S("Końcowa optymalizacja", "scene_stats", "Statystyki"))
    if img:
        p.notes.append("Kolory postaci pobrano z palety załączonego zdjęcia. Odwzorowanie kształtu 1:1 ze zdjęcia wymaga "
                       "zewnętrznego generatora image-to-3D (patrz Ustawienia → Generatory 3D).")
    return p


def game_house(prompt, att):
    wall = nlp.color_near(prompt, ("ścian", "scian", "wall", "dom", "house"), 25)
    roofc = nlp.color_near(prompt, ("dach", "roof"), 25) or "#7a2e22"
    p = Plan("create", "Dom", params={"roof_color": roofc})
    wm = {"preset": "brick"} if not wall else {"preset": "concrete", "color": wall}
    p.steps += [
        S("Fundament", "add_primitive", "Płyta fundamentowa", name="Foundation", shape="box", size=[8.4, 0.3, 6.4], position=[0, 0.15, 0],
          material={"preset": "concrete"}),
        S("Ściany", "add_primitive", "Bryła ścian", name="Walls", shape="box", size=[8, 3, 6], position=[0, 1.8, 0], material=wm),
        S("Dach", "add_extrusion", "Dach dwuspadowy", name="Roof", points=[[-4.5, 0], [4.5, 0], [0, 2.4]], depth=6.8,
          position=[0, 3.3, -3.4], material={"preset": "tiles", "color": "=roof_color"}),
        S("Dach", "add_primitive", "Komin", name="Chimney", shape="box", size=[0.6, 1.6, 0.6], position=[2.2, 4.8, 1.0],
          material={"preset": "brick"}),
        S("Drzwi i okna", "add_primitive", "Drzwi", name="Door", shape="box", size=[1.1, 2.1, 0.08], position=[0, 1.35, 3.02],
          material={"preset": "wood_planks", "color": "#6b4226"}),
    ]
    for i, x in enumerate((-2.7, 2.7)):
        p.steps += [S("Drzwi i okna", "add_primitive", f"Okno {i + 1}", name=f"Window{i + 1}", shape="box", size=[1.3, 1.2, 0.06],
                      position=[x, 2.0, 3.02], material={"preset": "glass", "color": "#8fb4cc", "opacity": 0.55}),
                    S("Drzwi i okna", "add_primitive", f"Rama okna {i + 1}", name=f"Frame{i + 1}", shape="box", size=[1.45, 1.35, 0.05],
                      position=[x, 2.0, 2.99], material={"preset": "wood", "color": "#f0ece4"})]
    p.steps += [S("Otoczenie", "add_primitive", "Schody", name="Step", shape="box", size=[1.6, 0.2, 0.6], position=[0, 0.1, 3.5],
                  material={"preset": "stone"}),
                S("Generowanie tekstur", "set_texture_resolution", "Tekstury 1024 px", resolution=1024),
                S("Końcowa optymalizacja", "scene_stats", "Statystyki")]
    return p


def game_tree(prompt, att):
    kind = "conifer" if nlp.has(prompt, "choink", "choin", "świerk", "swierk", "sosn", "pine", "conifer", "spruce") else "broadleaf"
    p = Plan("create", "Drzewo")
    p.steps += [S("Pień i korona", "add_tree", "Drzewo", name="Tree", height=6, kind=kind,
                  leaf_color=nlp.color_near(prompt, ("liś", "lis", "leaf", "koron"), 25) or "#3f7d2c"),
                S("Generowanie tekstur", "set_texture_resolution", "Tekstury 1024 px", resolution=1024)]
    return p


def game_chair(prompt, att):
    c = nlp.first_color(prompt) or "#8a5a33"
    p = Plan("create", "Krzesło", params={"wood": c})
    wm = {"preset": "wood", "color": "=wood"}
    p.steps.append(S("Siedzisko", "add_primitive", "Siedzisko", name="Seat", shape="rounded_box", size=[0.46, 0.05, 0.44], radius=0.015,
                     position=[0, 0.46, 0], material=wm))
    for i, (x, z) in enumerate([(-0.2, -0.19), (0.2, -0.19), (0.2, 0.19), (-0.2, 0.19)]):
        p.steps.append(S("Nogi", "add_primitive", f"Noga {i + 1}", name=f"Leg{i + 1}", shape="cylinder", radius=0.022, radius_top=0.018,
                         height=0.44, position=[x, 0.22, z], material=wm))
    p.steps += [S("Oparcie", "add_primitive", "Oparcie", name="Back", shape="rounded_box", size=[0.44, 0.38, 0.035], radius=0.012,
                  position=[0, 0.8, -0.2], rotation=[-8, 0, 0], material=wm),
                S("Oparcie", "add_primitive", "Słupek L", name="PostL", shape="cylinder", radius=0.018, height=0.45,
                  position=[-0.2, 0.68, -0.2], rotation=[-8, 0, 0], material=wm),
                S("Oparcie", "add_primitive", "Słupek R", name="PostR", shape="cylinder", radius=0.018, height=0.45,
                  position=[0.2, 0.68, -0.2], rotation=[-8, 0, 0], material=wm),
                S("Generowanie tekstur", "set_texture_resolution", "Tekstury 1024 px", resolution=1024)]
    if nlp.has(prompt, "poduszk", "tapicer", "cushion", "upholster"):
        p.steps.insert(1, S("Siedzisko", "add_primitive", "Poduszka", name="Cushion", shape="rounded_box", size=[0.42, 0.05, 0.4],
                            radius=0.02, position=[0, 0.505, 0], material={"preset": "fabric", "color": "#5a6b7d"}))
    return p


def game_table(prompt, att):
    c = nlp.first_color(prompt) or "#7a4e2d"
    p = Plan("create", "Stół", params={"wood": c})
    wm = {"preset": "wood_planks", "color": "=wood"}
    p.steps.append(S("Blat", "add_primitive", "Blat", name="Top", shape="rounded_box", size=[1.6, 0.05, 0.9], radius=0.012,
                     position=[0, 0.75, 0], material=wm))
    for i, (x, z) in enumerate([(-0.72, -0.38), (0.72, -0.38), (0.72, 0.38), (-0.72, 0.38)]):
        p.steps.append(S("Nogi", "add_primitive", f"Noga {i + 1}", name=f"Leg{i + 1}", shape="box", size=[0.06, 0.72, 0.06],
                         position=[x, 0.365, z], material={"preset": "wood", "color": "=wood"}))
    p.steps.append(S("Generowanie tekstur", "set_texture_resolution", "Tekstury 1024 px", resolution=1024))
    return p


def game_sword(prompt, att):
    p = Plan("create", "Miecz")
    p.steps += [
        S("Ostrze", "add_extrusion", "Ostrze", name="Blade", points=[[-0.03, 0], [0.03, 0], [0.026, 0.8], [0, 0.88], [-0.026, 0.8]],
          depth=0.008, position=[0, 0.12, -0.004], material={"preset": "chrome"}),
        S("Jelec", "add_primitive", "Jelec", name="Guard", shape="rounded_box", size=[0.22, 0.03, 0.04], radius=0.01,
          position=[0, 0.115, 0], material={"preset": "gold"}),
        S("Rękojeść", "add_primitive", "Rękojeść", name="Grip", shape="cylinder", radius=0.016, height=0.14, position=[0, 0.03, 0],
          material={"preset": "leather", "color": "#3b2416"}),
        S("Rękojeść", "add_primitive", "Głowica", name="Pommel", shape="sphere", radius=0.025, position=[0, -0.05, 0],
          material={"preset": "gold"}),
        S("Generowanie tekstur", "set_texture_resolution", "Tekstury 1024 px", resolution=1024)]
    return p


def game_crate(prompt, att):
    p = Plan("create", "Skrzynia")
    p.steps += [S("Bryła", "add_primitive", "Skrzynia", name="Crate", shape="box", size=[1, 1, 1], bevel=0.02, position=[0, 0.5, 0],
                  material={"preset": "wood_planks"})]
    for i, (y, ax) in enumerate([(0.03, 0), (0.97, 0)]):
        p.steps.append(S("Okucia", "add_primitive", f"Obręcz {i + 1}", name=f"Band{i + 1}", shape="box", size=[1.02, 0.06, 1.02],
                         position=[0, y, 0], material={"preset": "metal_painted", "color": "#3a3d42"}))
    p.steps.append(S("Generowanie tekstur", "set_texture_resolution", "Tekstury 1024 px", resolution=1024))
    return p


def game_barrel(prompt, att):
    p = Plan("create", "Beczka")
    prof = [[0.0, 0], [0.27, 0], [0.31, 0.2], [0.33, 0.45], [0.31, 0.7], [0.27, 0.9], [0.0, 0.9]]
    p.steps.append(S("Bryła", "add_lathe", "Korpus beczki (lathe)", name="Barrel", profile=prof, segments=40, material={"preset": "wood_planks"}))
    for i, (y, r) in enumerate([(0.12, 0.29), (0.45, 0.333), (0.78, 0.29)]):
        p.steps.append(S("Obręcze", "add_primitive", f"Obręcz {i + 1}", name=f"Hoop{i + 1}", shape="torus", radius=r, tube_radius=0.012,
                         position=[0, y, 0], material={"preset": "metal_painted", "color": "#2c2f33"}))
    p.steps.append(S("Generowanie tekstur", "set_texture_resolution", "Tekstury 1024 px", resolution=1024))
    return p


def game_scene(prompt, att):
    p = Plan("create", "Teren z drzewami")
    p.steps.append(S("Teren", "add_terrain", "Teren", name="Terrain", size=30, height=1.2))
    for i, (x, z, k) in enumerate([(-6, -4, "conifer"), (5, -6, "broadleaf"), (-3, 6, "broadleaf"), (8, 3, "conifer"), (0, -9, "conifer")]):
        p.steps.append(S("Roślinność", "add_tree", f"Drzewo {i + 1}", name=f"Tree{i + 1}", height=5 + i % 3, kind=k, position=[x, 0.6, z], seed=i))
    for i, (x, z, s) in enumerate([(2, 2, 0.8), (-7, 1, 0.5), (4, 8, 1.1)]):
        p.steps.append(S("Skały", "add_primitive", f"Skała {i + 1}", name=f"Rock{i + 1}", shape="sphere", radius=s, segments=12,
                         position=[x, 0.7, z], scale=[1.3, 0.7, 1.0], material={"preset": "stone"}))
    p.steps.append(S("Generowanie tekstur", "set_texture_resolution", "Tekstury 1024 px", resolution=1024))
    return p


CAD_TEMPLATES = [
    (("obudow", "enclosure", "case", "pudeł", "pudel", "skrzynk", "housing", "pojemnik"), cad_enclosure),
    (("płyt", "plyt", "plate", "panel", "podstawk"), cad_plate),
    (("tulej", "tuleja", "rura", "rurk", "dystans", "spacer", "tube", "pipe", "bushing"), cad_tube),
    (("uchwyt", "bracket", "kątownik", "katownik", "wspornik"), cad_bracket),
]
GAME_TEMPLATES = [
    (("samoch", "auto", "car", "pojazd", "vehicle", "bolid", "supercar"), game_car),
    (("postać", "postac", "character", "człowiek", "czlowiek", "humanoid", "bohater", "person", "avatar"), game_character),
    (("dom", "house", "budyn", "building", "chata", "cottage"), game_house),
    (("drzew", "tree", "choink", "sosn"), game_tree),
    (("krzesł", "krzesl", "chair"), game_chair),
    (("stół", "stol", "table", "biurk", "desk"), game_table),
    (("miecz", "sword"), game_sword),
    (("skrzyni", "crate"), game_crate),
    (("beczk", "barrel"), game_barrel),
    (("teren", "terrain", "mapa", "map", "scen", "las ", "forest", "środowisk", "environment"), game_scene),
]


# ============================================================================ edits
def plan_edit(prompt: str, mode: str, snap: dict, selected: str | None, att: list[dict]) -> Plan | None:
    t = nlp.norm(prompt)
    params = snap.get("params") or {}
    objs = {o["name"]: o for o in snap.get("objects", [])}
    p = Plan("edit", "Edycja modelu")

    # ---- user-parameter edits (dimensions)
    dim_words = {"H": ("wysoko", "height", "wyższ", "taller"), "W": ("szerok", "width", "szersz"),
                 "D": ("głębok", "glebok", "depth", "dług", "dlug", "length"),
                 "wall": ("ścian", "scian", "wall"), "corner_r": ("zaokrągl", "zaokragl", "promie", "radius", "fillet")}
    if mode == "cad":
        for key, words in dim_words.items():
            if any(w in t for w in words) and key in params:
                dm = nlp.delta_mm(t)
                if dm:
                    op, v = dm
                    new = params[key] + v if op == "inc" else params[key] - v if op == "dec" else v
                    upd = {key: round(new, 4)}
                    if key == "wall" and "lid" in params:
                        upd["lid"] = round(new, 4)
                    p.steps.append(S("Edycja parametrów", "__params", f"{key}: {params[key]} → {new:g} mm", updates=upd))
                    p.steps.append(S("Kontrola wymiarów", "measure", "Pomiar po zmianie", target=next(iter(objs), "")))
                    return p
    # ---- colours
    col = nlp.first_color(t)
    if col and nlp.has(t, "kolor", "zmień", "zmien", "pomaluj", "ustaw", "color", "colour", "make", "zrób", "zrob", "na "):
        if mode == "game":
            if nlp.has(t, "feln", "felg", "rim", "wheel") and "rim" in params:
                p.steps.append(S("Materiały", "__params", f"Felgi → {col}", updates={"rim": col}))
                return p
            if nlp.has(t, "wnętrz", "wnetrz", "interior", "fotel", "seat") and "interior" in params:
                p.steps.append(S("Materiały", "__params", f"Wnętrze → {col}", updates={"interior": col}))
                return p
            if nlp.has(t, "zacisk", "caliper") and "caliper" in params:
                p.steps.append(S("Materiały", "__params", f"Zaciski → {col}", updates={"caliper": col}))
                return p
            if selected and selected in objs:
                p.steps.append(S("Materiały", "set_material", f"Kolor {selected} → {col}", target=selected, color=col))
                return p
            if "paint" in params:
                p.steps.append(S("Materiały", "__params", f"Lakier → {col}", updates={"paint": col}))
                return p
            for k in ("c_shirt", "roof_color", "wood"):
                if k in params:
                    p.steps.append(S("Materiały", "__params", f"{k} → {col}", updates={k: col}))
                    return p
            first = next((n for n, o in objs.items() if o.get("parent") is None), None)
            if first:
                p.steps.append(S("Materiały", "set_material", f"Kolor {first} → {col}", target=first, color=col))
                return p
        else:
            if selected and selected in objs:
                p.steps.append(S("Kolor", "set_color", f"Kolor {selected} → {col}", target=selected, color=col))
                return p
            if "color" in params:
                p.steps.append(S("Kolor", "__params", f"Kolor → {col}", updates={"color": col}))
                return p
            for n in objs:
                p.steps.append(S("Kolor", "set_color", f"Kolor {n} → {col}", target=n, color=col))
            return p
    # ---- delete selected
    if nlp.has(t, r"\busuń\b", r"\busun\b", "delete", "remove", "skasuj") and selected:
        p.steps.append(S("Edycja", "delete" if mode == "game" else "delete_body", f"Usunięcie {selected}", target=selected))
        return p
    # ---- platform optimisation
    for plat in ("roblox", "unity", "unreal", "web", "mobile"):
        if plat in t and mode == "game":
            p.steps.append(S("Optymalizacja", "optimize_for", f"Optymalizacja pod {plat}", platform=plat))
            return p
    # ---- textures / detail
    if mode == "game" and nlp.has(t, "tekstur", "texture", "realisty"):
        p.steps.append(S("Tekstury", "set_texture_resolution", "Tekstury 2048 px (więcej detalu PBR)", resolution=2048))
        return p
    if mode == "game" and nlp.has(t, "więcej szczeg", "wiecej szczeg", "more detail", "detal"):
        tgt = selected or ("Body" if "Body" in objs else None)
        if tgt and objs.get(tgt, {}).get("triangles"):
            p.steps.append(S("Detale", "smooth", f"Wygładzenie/subdywizja {tgt}", target=tgt, iterations=1))
        p.steps.append(S("Tekstury", "set_texture_resolution", "Tekstury 2048 px", resolution=2048))
        return p
    # ---- logo / decal on existing model
    img = next((a for a in att if a["mime"].startswith("image/")), None)
    if img and nlp.has(t, "logo", "grafik", "naklej", "decal", "obraz"):
        if mode == "game":
            tgt = selected or ("Body" if "Body" in objs else next((n for n, o in objs.items() if o.get("triangles")), None))
            if tgt:
                sides = ("+z", "-z") if nlp.has(t, "drzw", "door", "bok", "side") else ("+y",)
                for sd in sides:
                    p.steps.append(S("Logo", "apply_decal", f"Logo na {tgt} ({sd})", target=tgt, attachment_id=img["id"], side=sd,
                                     center=[0.5, 0.55], width=0.5))
                return p
        else:
            tgt = selected or ("Lid" if "Lid" in objs else next(iter(objs), None))
            if tgt:
                eng = nlp.has(t, "grawer", "engrav")
                p.steps.append(S("Logo", "apply_image", "Logo z załącznika", target=tgt, attachment_id=img["id"], face=">Z",
                                 mode="engrave" if eng else "emboss",
                                 depth=nlp.mm_after(t, r"(?:głębokoś|glebokos|depth)") or (0.5 if eng else 0.6)))
                return p
    # ---- fillet / chamfer in CAD
    if mode == "cad" and nlp.has(t, "zaokrągl", "zaokragl", "fillet") and (selected or objs):
        r = nlp.mm_after(t, r"(?:promie|radius|r\b|o\b)") or 1.0
        p.steps.append(S("Krawędzie", "fillet", f"Zaokrąglenie R{r}", target=selected or next(iter(objs)), radius=r, edges="|Z"))
        return p
    if mode == "cad" and nlp.has(t, "fazk", "faz", "chamfer"):
        r = nlp.mm_after(t, r"(?:faz|chamfer)") or 0.5
        p.steps.append(S("Krawędzie", "chamfer", f"Faza {r} mm", target=selected or next(iter(objs)), distance=r, edges=">Z"))
        return p
    return None


def plan(prompt: str, mode: str, snap: dict, selected: str | None, att: list[dict]) -> Plan:
    t = nlp.norm(prompt)
    has_model = bool(snap.get("objects"))
    creating = nlp.has(t, "stwórz", "stworz", "wygeneruj", "zrób", "zrob", "zbuduj", "utwórz", "utworz", "create", "generate",
                       "make", "build", "model", "zaprojektuj", "design", "nowy", "nową", "nowa")
    if has_model and not (creating and not nlp.has(t, "dodaj", "add", "zmień", "zmien", "zwiększ", "zmniejsz")):
        e = plan_edit(prompt, mode, snap, selected, att)
        if e:
            return e
    elif has_model:
        e = plan_edit(prompt, mode, snap, selected, att)
        if e and not creating:
            return e
    templates = CAD_TEMPLATES if mode == "cad" else GAME_TEMPLATES
    for words, fn in templates:
        if any(w in t for w in words):
            return fn(prompt, att)
    if mode == "cad" and nlp.dims3(t):
        return cad_box_generic(prompt, att)
    if has_model:
        e = plan_edit(prompt, mode, snap, selected, att)
        if e:
            return e
    known = ", ".join(w[0][0] for w in templates)
    return Plan("unknown", "Nierozpoznane polecenie", notes=[
        f"Planer offline (bez modelu AI) rozpoznaje tylko wybrane rodziny obiektów ({known}…) i typowe edycje. "
        "Aby generować dowolne obiekty, dodaj dostawcę AI (Claude / GPT / Gemini / Ollama) w Ustawieniach → Modele AI."])
