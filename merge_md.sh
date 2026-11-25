#!/usr/bin/env bash
# Объединяет все report.md из каталога reports/ в один markdown-файл.
# Использование: ./merge_md.sh <reports_dir> [output.md]
set -euo pipefail

REPORTS_DIR="${1:?Укажите путь к каталогу reports/}"
OUTPUT="${2:-$REPORTS_DIR/park_summary.md}"

if [ ! -d "$REPORTS_DIR" ]; then
  echo "Каталог не найден: $REPORTS_DIR" >&2
  exit 1
fi

{
  echo "# Сводный отчёт по парку"
  echo ""
  echo "_Сформирован: $(date -Iseconds)_"
  echo ""
  echo "_Источников: $(find "$REPORTS_DIR" -name report.md | wc -l)_"
  echo ""

  first=1
  while IFS= read -r -d '' md; do
    if [ "$first" -eq 1 ]; then
      first=0
    else
      echo ""
      echo "---"
      echo ""
    fi
    cat "$md"
    echo ""
  done < <(find "$REPORTS_DIR" -name report.md -print0 | sort -z)

} > "$OUTPUT"

echo "Готово: $OUTPUT ($(wc -l < "$OUTPUT") строк)"
