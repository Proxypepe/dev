#!/usr/bin/env bash
# Сбор аппаратного аудита. Запуск: sudo ./hw_audit.sh [каталог_отчёта]
set -uo pipefail

OUT="${1:-hw-audit-$(hostname)-$(date +%Y%m%d-%H%M%S)}"
RAW="$OUT/raw"
mkdir -p "$RAW"

need() { command -v "$1" >/dev/null 2>&1 || { echo "Не найдено: $1"; exit 1; }; }
need python3; need jq; need lsblk; need lscpu; need lspci
[[ $EUID -eq 0 ]] || { echo "Запустите через sudo (нужен dmidecode)"; exit 1; }

# Сохраняем вывод, не прерывая сбор при ошибке отдельной утилиты
snap() {
  local file="$1"; shift
  "$@" >"$RAW/$file" 2>"$RAW/$file.err" || true
}

snap hostname.txt        hostname
snap os-release.txt      cat /etc/os-release
snap kernel.txt          uname -r
snap virt.txt            systemd-detect-virt
snap lscpu.json          lscpu -J
snap meminfo.txt         cat /proc/meminfo
snap lsblk.json          lsblk -J -b -o NAME,PATH,TYPE,MODEL,SERIAL,VENDOR,SIZE,PHY-SEC,ROTA,TRAN,WWN,REV,MOUNTPOINT
snap lspci.txt           lspci -nnk
snap mdstat.txt          cat /proc/mdstat
snap sys-block.txt       ls -l /sys/block

command -v dmidecode >/dev/null && {
  snap dmi-memory.txt    dmidecode -t memory
  snap dmi-system.txt    dmidecode -t system
}
command -v lshw >/dev/null && snap lshw.json lshw -json -class disk -class storage
command -v nvme >/dev/null && snap nvme.json nvme list -o json

# SMART по физическим устройствам
if command -v smartctl >/dev/null; then
  for p in /sys/block/*; do
    n="${p##*/}"
    case "$n" in
      sd*|nvme*|vd*|xvd*|mmcblk*) snap "smart-$n.txt" smartctl -a -T permissive "/dev/$n" ;;
    esac
  done
fi

# Аппаратные RAID-контроллеры: сохраняем всё, что доступно
for spec in \
  "storcli64|/c0 show J|raid-storcli.json" \
  "storcli|/c0 show J|raid-storcli.json" \
  "perccli64|/c0 show J|raid-perccli.json" \
  "ssacli|ctrl all show config|raid-ssacli.txt" \
  "hpacucli|ctrl all show config|raid-ssacli.txt" \
  "arcconf|getconfig 1|raid-arcconf.txt" \
  "MegaCli64|-LDInfo -Lall -aALL|raid-megacli.txt"
do
  IFS='|' read -r bin args dest <<< "$spec"
  if command -v "$bin" >/dev/null 2>&1; then
    # shellcheck disable=SC2086
    snap "$dest" "$bin" $args
  fi
done

echo "Сырые данные: $RAW"
echo "Далее: python3 normalize.py $OUT"
