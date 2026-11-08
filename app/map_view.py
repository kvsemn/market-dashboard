import json
from pathlib import Path

from nicegui import ui


FEATURES = json.loads(
    Path(__file__).with_name("assets").joinpath("russia.json").read_text()
)["features"]


def inside_ring(x, y, ring):
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][:2]
        xj, yj = ring[j][:2]
        if (yi > y) != (yj > y):
            crossing = (xj - xi) * (y - yi) / (yj - yi) + xi
            if x < crossing:
                inside = not inside
        j = i
    return inside


def contains(feature, x, y):
    geometry = feature["geometry"]
    polygons = (
        [geometry["coordinates"]]
        if geometry["type"] == "Polygon"
        else geometry["coordinates"]
    )
    return any(
        inside_ring(x, y, polygon[0])
        and not any(
            inside_ring(x, y, hole) for hole in polygon[1:]
        )
        for polygon in polygons
    )


def build_map(region, names):
    ui.add_css("""
        .leaflet-control-attribution a[href="https://leafletjs.com"] svg {
            display: none;
        }
    """)
    available = set(names) - {""}

    with ui.card().classes("w-full"):
        ui.label("Карта России").classes("text-xl font-semibold")
        ui.label(
            "Синий — есть данные · Оранжевый — выбранный регион · "
            "Серый — нет данных"
        ).classes("text-sm text-slate-500")

        selected = ui.label(
            f'Выбран регион: {names.get(region.value, "Все регионы")}'
        )

        m = ui.leaflet(
            center=(64, 100),
            zoom=2,
            options={
                "attributionControl": False,
                "minZoom": 2,
                "maxZoom": 9,
                "scrollWheelZoom": False,
                "doubleClickZoom": False,
            },
        ).classes("w-full").style(
            "height: clamp(360px, 55vh, 600px); background: #eef2f7;"
        )
        m.clear_layers()

        layers = []

        def style(code):
            active = code == region.value
            has_data = code in available
            return {
                "color": "#ffffff",
                "weight": 1,
                "fillColor": (
                    "#f59e0b" if active
                    else "#2563eb" if has_data
                    else "#cbd5e1"
                ),
                "fillOpacity": 0.85,
            }

        for feature in FEATURES:
            code = feature["properties"].get("ISO")
            layer = m.generic_layer(
                name="geoJSON",
                args=[
                    feature,
                    {
                        "style": style(code),
                        "interactive": False,
                    },
                ],
            )
            layers.append((code, layer))

        def choose(event):
            point = event.args.get("latlng", {})
            if "lat" not in point or "lng" not in point:
                return

            x = point["lng"] % 360
            y = point["lat"]

            # Регионы с данными проверяем первыми:
            # это помогает при пересечении геометрий городов и областей.
            ordered = sorted(
                FEATURES,
                key=lambda f: (
                    f["properties"].get("ISO") not in available
                ),
            )
            for feature in ordered:
                if contains(feature, x, y):
                    code = feature["properties"].get("ISO")
                    if code in available:
                        region.set_value(code)
                    else:
                        ui.notify("В этом регионе пока нет данных.")
                    return

        def sync(_):
            selected.set_text(
                f'Выбран регион: {names.get(region.value, "Все регионы")}'
            )
            for code, layer in layers:
                layer.run_method("setStyle", style(code))

        m.on("map-click", choose)
        region.on_value_change(sync)

        ui.button(
            "Все регионы",
            on_click=lambda: region.set_value(""),
        ).props("outline")

        ui.label(
            "Геометрия: набор карт Apache Superset 5.0.0. "
            "Цвет обозначает наличие данных, а не уровень риска."
        ).classes("text-xs text-slate-500")
