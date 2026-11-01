#!/usr/bin/env python3
"""Sinh cấu hình Home Assistant cho 21 điểm đo từ points.csv.

Chạy:  python3 tools/generate.py
Kết quả: homeassistant/packages/smartlab_points.yaml
         homeassistant/dashboards/smartlab.yaml

Sửa points.csv (tên, ngưỡng, hệ số hiệu chuẩn) rồi chạy lại, không sửa tay file sinh ra.
"""
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POINTS = ROOT / "points.csv"
PKG = ROOT / "homeassistant" / "packages" / "smartlab_points.yaml"
DASH = ROOT / "homeassistant" / "dashboards" / "smartlab.yaml"

HEADER = "# FILE TỰ SINH bởi tools/generate.py từ points.csv. Không sửa tay.\n"


AUTOMATIONS = """
automation:
  - id: smartlab_canh_bao_nguong
    alias: SmartLab – báo Zalo khi vượt ngưỡng / hết vượt ngưỡng
    mode: queued
    trigger:
      - platform: state
        entity_id:
{alarms}        from: ["off", "on"]
        to: ["on", "off"]
    action:
      - action: script.smartlab_zalo
        data:
          message: >-
            {{% if trigger.to_state.state == 'on' %}}🔴 CẢNH BÁO {{{{ trigger.to_state.attributes.diem_do }}}}:
            {{{{ trigger.to_state.attributes.gia_tri }}}} (ngưỡng {{{{ trigger.to_state.attributes.nguong }}}}).
            Đề nghị kiểm tra ngay.{{% else %}}✅ ĐÃ BÌNH THƯỜNG {{{{ trigger.to_state.attributes.diem_do }}}}:
            {{{{ trigger.to_state.attributes.gia_tri }}}}.{{% endif %}}
            ({{{{ now().strftime('%H:%M %d/%m') }}}})

  - id: smartlab_mat_tin_hieu
    alias: SmartLab – báo Zalo khi cảm biến mất tín hiệu
    mode: queued
    trigger:
      - platform: state
        entity_id:
{offline}        from: ["off", "on"]
        to: ["on", "off"]
    action:
      - action: script.smartlab_zalo
        data:
          message: >-
            {{% if trigger.to_state.state == 'on' %}}⚠️ MẤT TÍN HIỆU {{{{ trigger.to_state.attributes.diem_do }}}} quá 60 phút
            (kiểm tra pin / sóng Zigbee / nguồn).{{% else %}}✅ Có tín hiệu lại: {{{{ trigger.to_state.attributes.diem_do }}}}.{{% endif %}}
            ({{{{ now().strftime('%H:%M %d/%m') }}}})

  - id: smartlab_nhac_lai
    alias: SmartLab – nhắc lại mỗi 30 phút khi còn cảnh báo
    trigger:
      - platform: time_pattern
        minutes: "/30"
    condition:
      - condition: state
        entity_id: group.smartlab_alarms
        state: "on"
    action:
      - action: script.smartlab_zalo
        data:
          message: >-
            🔁 VẪN ĐANG CẢNH BÁO:
            {{% for e in expand('group.smartlab_alarms') | selectattr('state', 'eq', 'on') %}}
            • {{{{ e.attributes.diem_do }}}}: {{{{ e.attributes.gia_tri }}}} (từ {{{{ as_local(e.last_changed).strftime('%H:%M') }}}})
            {{% endfor %}}

  - id: smartlab_ghi_so_dinh_ky
    alias: SmartLab – ghi sổ theo dõi nhiệt độ/độ ẩm/EC (ISO 15189)
    trigger:
      - platform: time
        at: ["07:30:00", "13:30:00", "19:30:00"]
    action:
      - action: script.smartlab_ghi_so
"""


def q(s):
    """Chuỗi YAML an toàn (JSON là YAML hợp lệ)."""
    return json.dumps(s, ensure_ascii=False)


def load():
    with POINTS.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    ids = [r["id"] for r in rows]
    assert len(ids) == len(set(ids)), "Trùng id trong points.csv"
    return rows


def raw_entities(p):
    """Entity gốc mà thiết bị phải được đổi tên thành sau khi ghép nối."""
    if p["kind"] == "ro":
        return {"ec": f"sensor.{p['id']}_ec"}
    out = {"t": f"sensor.{p['id']}_raw_temperature"}
    if p["kind"] == "room":
        out["h"] = f"sensor.{p['id']}_raw_humidity"
    return out


def calibrated(raw, offset, digits=1):
    """Giá trị đã cộng hệ số hiệu chuẩn + điều kiện có dữ liệu."""
    state = f"{{{{ (states('{raw}') | float(0) + {float(offset or 0)}) | round({digits}) }}}}"
    avail = f"{{{{ is_number(states('{raw}')) }}}}"
    return f"{q(state)}\n        availability: {q(avail)}"



def build_package(points):
    sensors, binaries, log_lines, alarm_ids, offline_ids = [], [], [], [], []
    for p in points:
        pid, name, raw = p["id"], p["name"], raw_entities(p)
        delay = int(p["delay_min"] or 15)
        checks = []  # (entity cal, lo, hi, label)
        if p["kind"] == "ro":
            ent = f"sensor.smartlab_{pid}_ec"
            sensors.append(
                f"      - name: {q(f'SmartLab {pid} EC')}\n"
                f"        unique_id: smartlab_{pid}_ec\n"
                f"        unit_of_measurement: \"µS/cm\"\n"
                f"        state_class: measurement\n"
                f"        state: {calibrated(raw['ec'], p['t_offset'], 2)}\n"
            )
            checks.append((ent, p["t_min"], p["t_max"], "EC"))
            log_lines.append(f"{name}: {{{{ states('{ent}') }}}} µS/cm")
        else:
            ent_t = f"sensor.smartlab_{pid}_nhiet_do"
            sensors.append(
                f"      - name: {q(f'SmartLab {pid} nhiệt độ')}\n"
                f"        unique_id: smartlab_{pid}_temp\n"
                f"        device_class: temperature\n"
                f"        unit_of_measurement: \"°C\"\n"
                f"        state_class: measurement\n"
                f"        state: {calibrated(raw['t'], p['t_offset'])}\n"
            )
            checks.append((ent_t, p["t_min"], p["t_max"], "nhiệt độ"))
            line = f"{name}: {{{{ states('{ent_t}') }}}} °C"
            if "h" in raw:
                ent_h = f"sensor.smartlab_{pid}_do_am"
                sensors.append(
                    f"      - name: {q(f'SmartLab {pid} độ ẩm')}\n"
                    f"        unique_id: smartlab_{pid}_hum\n"
                    f"        device_class: humidity\n"
                    f"        unit_of_measurement: \"%\"\n"
                    f"        state_class: measurement\n"
                    f"        state: {calibrated(raw['h'], p['h_offset'])}\n"
                )
                checks.append((ent_h, p["h_min"], p["h_max"], "độ ẩm"))
                line += f", {{{{ states('{ent_h}') }}}} %"
            log_lines.append(line)

        # Ghép các điều kiện thành một template duy nhất
        cond_tpl = "{% set ns = namespace(bad=false) %}" + "".join(
            f"{{% set v = states('{e}') %}}"
            f"{{% if is_number(v) and (v | float < {float(lo)} or v | float > {float(hi)}) %}}"
            f"{{% set ns.bad = true %}}{{% endif %}}"
            for e, lo, hi, _ in checks
        ) + "{{ ns.bad }}"
        limits = "; ".join(f"{lab} {lo}…{hi}" for _, lo, hi, lab in checks)
        values = ", ".join(f"{lab} {{{{ states('{e}') }}}}" for e, _, _, lab in checks)
        binaries.append(
            f"      - name: {q(f'Cảnh báo {pid}')}\n"
            f"        unique_id: smartlab_{pid}_alarm\n"
            f"        device_class: problem\n"
            f"        delay_on: {{minutes: {delay}}}\n"
            f"        delay_off: {{minutes: 2}}\n"
            f"        state: {q(cond_tpl)}\n"
            f"        attributes:\n"
            f"          diem_do: {q(name)}\n"
            f"          nguong: {q(limits)}\n"
            f"          gia_tri: {q(values)}\n"
        )
        alarm_ids.append(f"binary_sensor.canh_bao_{pid}")
        first_raw = next(iter(raw.values()))
        binaries.append(
            f"      - name: {q(f'Mất tín hiệu {pid}')}\n"
            f"        unique_id: smartlab_{pid}_offline\n"
            f"        device_class: problem\n"
            f"        delay_on: {{minutes: 60}}\n"
            f"        state: {q(f'{{{{ states({first_raw!r}) in [\"unavailable\", \"unknown\"] }}}}')}\n"
            f"        attributes:\n"
            f"          diem_do: {q(name)}\n"
        )
        offline_ids.append(f"binary_sensor.mat_tin_hieu_{pid}")

    pkg = HEADER
    pkg += "template:\n  - sensor:\n" + "".join(sensors)
    pkg += "  - binary_sensor:\n" + "".join(binaries)
    pkg += "\ngroup:\n"
    pkg += "  smartlab_alarms:\n    name: SmartLab cảnh báo ngưỡng\n    entities:\n"
    pkg += "".join(f"      - {e}\n" for e in alarm_ids)
    pkg += "  smartlab_offline:\n    name: SmartLab mất tín hiệu\n    entities:\n"
    pkg += "".join(f"      - {e}\n" for e in offline_ids)
    pkg += AUTOMATIONS.format(
        alarms="".join(f"          - {e}\n" for e in alarm_ids),
        offline="".join(f"          - {e}\n" for e in offline_ids),
    )
    pkg += "\nscript:\n  smartlab_ghi_so:\n    alias: SmartLab ghi sổ theo dõi\n    sequence:\n"
    pkg += "      - action: notify.send_message\n        target:\n          entity_id: notify.smartlab_so_theo_doi\n"
    pkg += "        data:\n          message: >-\n"
    pkg += "            {{ now().strftime('%Y-%m-%d %H:%M') }} | "
    pkg += " | ".join(log_lines) + "\n"
    PKG.write_text(pkg, encoding="utf-8")


def build_dashboard(points):
    groups = [("Tủ lạnh / tủ đông", "fridge"), ("Phòng xét nghiệm", "room"), ("Nước RO", "ro")]
    out = HEADER + "title: SmartLab – Khoa Hóa Sinh\nviews:\n"
    out += "  - title: Tổng quan\n    path: tong-quan\n    cards:\n"
    out += (
        "      - type: entity-filter\n        title: ĐANG CẢNH BÁO\n"
        "        state_filter: ['on']\n        show_empty: false\n        entities:\n"
    )
    for p in points:
        out += f"          - binary_sensor.canh_bao_{p['id']}\n"
        out += f"          - binary_sensor.mat_tin_hieu_{p['id']}\n"
    for title, kind in groups:
        out += f"      - type: grid\n        title: {q(title)}\n        columns: {6 if kind != 'ro' else 3}\n        square: false\n        cards:\n"
        for p in (x for x in points if x["kind"] == kind):
            ent = f"sensor.smartlab_{p['id']}_ec" if kind == "ro" else f"sensor.smartlab_{p['id']}_nhiet_do"
            out += f"          - type: sensor\n            entity: {ent}\n            name: {q(p['name'])}\n            graph: line\n            hours_to_show: 24\n"
    out += "  - title: Biểu đồ\n    path: bieu-do\n    cards:\n"
    for title, kind in groups:
        ents = []
        for p in (x for x in points if x["kind"] == kind):
            if kind == "ro":
                ents.append(f"sensor.smartlab_{p['id']}_ec")
            else:
                ents.append(f"sensor.smartlab_{p['id']}_nhiet_do")
                if kind == "room":
                    ents.append(f"sensor.smartlab_{p['id']}_do_am")
        out += f"      - type: history-graph\n        title: {q(title + ' – 7 ngày')}\n        hours_to_show: 168\n        entities:\n"
        out += "".join(f"          - {e}\n" for e in ents)
    DASH.write_text(out, encoding="utf-8")


if __name__ == "__main__":
    pts = load()
    build_package(pts)
    build_dashboard(pts)
    print(f"OK: {len(pts)} điểm đo -> {PKG.name}, {DASH.name}")
