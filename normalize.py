#!/usr/bin/env python3
import json, re, csv, sys
from pathlib import Path
from datetime import datetime, timezone

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
RAW = OUT / "raw"
CSV = OUT / "csv"
CSV.mkdir(exist_ok=True)
WARN = []


def txt(name):
    p = RAW / name
    return p.read_text(errors="replace") if p.exists() else ""


def js(name, default=None):
    try:
        return json.loads(txt(name))
    except Exception:
        return default


def clean(v):
    if v is None:
        return None
    v = str(v).strip()
    return None if v.lower() in {"", "n/a", "none", "unknown", "not specified",
                                 "not present", "to be filled by o.e.m.", "default string"} else v


def num(v):
    m = re.search(r"[\d.]+", str(v or ""))
    return float(m.group()) if m else None


def size_bytes(v):
    m = re.match(r"\s*([\d.]+)\s*(B|KB|KIB|MB|MIB|GB|GIB|TB|TIB)?", str(v or ""), re.I)
    if not m:
        return None
    mult = {"": 1, "B": 1, "KB": 10**3, "KIB": 2**10, "MB": 10**6, "MIB": 2**20,
            "GB": 10**9, "GIB": 2**30, "TB": 10**12, "TIB": 2**40}
    return int(float(m.group(1)) * mult[m.group(2).upper() if m.group(2) else ""])


def write_csv(name, rows, fields):
    with (CSV / name).open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


# ---------- SERVER ----------
osr = dict(line.split("=", 1) for line in txt("os-release.txt").splitlines() if "=" in line)
server = {
    "hostname": clean(txt("hostname.txt")),
    "os": clean(osr.get("PRETTY_NAME", "").strip('"')),
    "kernel": clean(txt("kernel.txt")),
    "virtualization": clean(txt("virt.txt")) or "unknown",
    "manufacturer": None, "model": None, "serial": None,
}
for line in txt("dmi-system.txt").splitlines():
    k, _, v = line.partition(":")
    k, v = k.strip(), clean(v)
    if k == "Manufacturer": server["manufacturer"] = v
    if k == "Product Name":  server["model"] = v
    if k == "Serial Number": server["serial"] = v

if server["virtualization"] not in {"none", "unknown", None}:
    WARN.append("Хост виртуализирован: DMI/диски могут быть эмуляцией гипервизора")

# ---------- CPU ----------
lc = js("lscpu.json", {})
f = {x.get("field", "").rstrip(":").strip(): x.get("data") for x in lc.get("lscpu", [])}
sockets = int(num(f.get("Socket(s)")) or 0) or None
cps = int(num(f.get("Core(s) per socket")) or 0) or None
tpc = int(num(f.get("Thread(s) per core")) or 0) or None
cpu = {
    "model": clean(f.get("Model name")),
    "sockets": sockets,
    "cores_per_socket": cps,
    "threads_per_core": tpc,
    "physical_cores_total": sockets * cps if sockets and cps else None,
    "logical_cpus_total": int(num(f.get("CPU(s)")) or 0) or None,
    "max_frequency_mhz": num(f.get("CPU max MHz")),
}

# ---------- MEMORY ----------
def dmi_blocks(text):
    return re.split(r"\n(?=Handle\s)", text)


modules, slots = [], 0
for block in dmi_blocks(txt("dmi-memory.txt")):
    if "Memory Device" not in block:
        continue
    slots += 1
    kv = {}
    for line in block.splitlines():
        k, _, v = line.partition(":")
        kv[k.strip()] = v.strip()
    if not kv.get("Size") or "No Module Installed" in kv["Size"]:
        continue
    modules.append({
        "locator": clean(kv.get("Locator")),
        "bank": clean(kv.get("Bank Locator")),
        "manufacturer": clean(kv.get("Manufacturer")),
        "part_number": clean(kv.get("Part Number")),
        "serial": clean(kv.get("Serial Number")),
        "size_bytes": size_bytes(kv.get("Size")),
        "type": clean(kv.get("Type")),
        "form_factor": clean(kv.get("Form Factor")),
        "speed_mts": num(kv.get("Speed")),
        "configured_speed_mts": num(kv.get("Configured Memory Speed") or kv.get("Configured Clock Speed")),
        "rank": clean(kv.get("Rank")),
    })

mt = re.search(r"MemTotal:\s+(\d+)\s+kB", txt("meminfo.txt"))
memory = {
    "os_total_bytes": int(mt.group(1)) * 1024 if mt else None,
    "dimm_slots_total": slots or None,
    "dimm_slots_populated": len(modules) or None,
    "modules_total_bytes": sum(m["size_bytes"] or 0 for m in modules) or None,
    "modules": modules,
}
if not modules:
    WARN.append("Модули памяти не найдены в DMI (VM или ограничен доступ)")

# ---------- DISKS ----------
flat = []
def walk(nodes, parent=None):
    for n in nodes or []:
        n = dict(n)
        n["_parent"] = parent
        flat.append(n)
        walk(n.get("children"), n.get("name"))
walk(js("lsblk.json", {}).get("blockdevices", []))

physical, logical = [], []
for n in flat:
    name, typ = n.get("name", ""), n.get("type")
    if typ == "disk" and re.match(r"^(sd|nvme|vd|xvd|mmcblk)", name):
        rota = n.get("rota")
        if rota == 1:
            media = "HDD"
        elif rota == 0:
            media = "NVMe SSD" if name.startswith("nvme") else "SSD"
        else:
            media = None
        smart = None
        for line in txt(f"smart-{name}.txt").splitlines():
            if "SMART overall-health" in line:
                smart = line.split(":", 1)[-1].strip()
        serial = clean(n.get("serial"))
        wwn = clean(n.get("wwn"))
        physical.append({
            "id": serial or wwn or name,
            "os_name": name,
            "parent": n["_parent"],
            "vendor": clean(n.get("vendor")),
            "model": clean(n.get("model")),
            "serial": serial,
            "wwn": wwn,
            "size_bytes": n.get("size"),
            "media_type": media,
            "interface": clean(n.get("tran")) or ("NVMe" if name.startswith("nvme") else None),
            "firmware": clean(n.get("rev")),
            "smart_health": smart,
            "is_virtual": server["virtualization"] not in {"none", None},
        })
    elif typ not in {"disk", "part"} or name.startswith(("md", "dm-")):
        logical.append({
            "os_name": name, "type": typ, "parent": n["_parent"],
            "size_bytes": n.get("size"), "mountpoints": n.get("mountpoint"),
        })

# ---------- CONTROLLERS ----------
controllers = []
for block in re.split(r"\n\s*\n", txt("lspci.txt")):
    lines = block.strip().splitlines()
    if not lines:
        continue
    m = re.match(r"^(\S+)\s+(.+?)\s+\[([0-9a-fA-F:]+)\]", lines[0])
    if not m:
        continue
    cls = m.group(2)
    if not re.search(r"raid|storage|sata|scsi|nvme", cls, re.I):
        continue
    controllers.append({
        "pci_slot": m.group(1), "class": cls, "device_id": m.group(3),
        "driver": next((l.split(":", 1)[1].strip() for l in lines
                        if "Kernel driver in use" in l), None),
    })

# ---------- RAID ----------
raid_devices = []
for line in txt("mdstat.txt").splitlines():
    m = re.match(r"^(md\S+)\s*:\s*(\w+)\s+(\w+)", line)
    if not m:
        continue
    members = re.findall(r"(\S+\d+)\[\d+\]", line)
    raid_devices.append({
        "name": m.group(1), "state": m.group(2), "level": m.group(3).lower(),
        "members": members, "degraded": bool(re.search(r"\([^)]*\)|_", line)),
    })

hw_raw = any(txt(x).strip() for x in
             ["raid-storcli.json", "raid-perccli.json", "raid-ssacli.txt",
              "raid-arcconf.txt", "raid-megacli.txt"])
hw_pci = any(re.search(r"\braid\b", c["class"], re.I) for c in controllers)
mode = "hardware" if (hw_raw or hw_pci) else ("software" if raid_devices else "none")
levels = {d["level"] for d in raid_devices}
raid = {
    "present": mode in {"hardware", "software"},
    "mode": mode,
    "level": next(iter(levels)) if len(levels) == 1 else ("mixed" if levels else None),
    "devices": raid_devices,
    "note": "Для HW RAID уровень/состояние ВД читайте из raw/raid-* (JSON утилиты вендора)",
}

# ---------- OUTPUT ----------
report = {
    "schema_version": "1.0",
    "collected_at": datetime.now(timezone.utc).isoformat(),
    "server": server, "cpu": cpu, "memory": memory,
    "storage": {"physical_disks": physical, "logical_volumes": logical,
                "controllers": controllers},
    "raid": raid, "warnings": WARN,
}
(OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

write_csv("server.csv", [server], list(server))
write_csv("cpu.csv", [cpu], list(cpu))
write_csv("memory_modules.csv", modules,
          ["locator", "bank", "manufacturer", "part_number", "serial", "size_bytes",
           "type", "form_factor", "speed_mts", "configured_speed_mts", "rank"])
write_csv("disks_physical.csv", physical,
          ["id", "os_name", "parent", "vendor", "model", "serial", "wwn", "size_bytes",
           "media_type", "interface", "firmware", "smart_health", "is_virtual"])
write_csv("logical_volumes.csv", logical, ["os_name", "type", "parent", "size_bytes", "mountpoints"])
write_csv("controllers.csv", controllers, ["pci_slot", "class", "device_id", "driver"])
write_csv("raid_devices.csv", raid_devices, ["name", "state", "level", "members", "degraded"])

print(f"Готово: {OUT / 'report.json'}")
print(f"CSV: {CSV}")
for w in WARN:
    print("WARNING:", w)
