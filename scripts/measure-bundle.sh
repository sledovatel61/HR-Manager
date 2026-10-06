#!/usr/bin/env bash
#
# Размер прод-сборки фронтенда: байты файлов и gzip.
#
# Появился после ревью PR #49: размеры, снятые «на глаз» из вывода vite,
# разошлись с фактом в четырёх цифрах из четырёх, и порог 15 % оказался
# посчитан по заниженной базе. Теперь числа снимаются только этим скриптом.
#
# Запуск из корня репозитория:
#
#   ./scripts/measure-bundle.sh                 # сборка в ./frontend
#   ./scripts/measure-bundle.sh /tmp/base/frontend   # сборка в другом дереве
#
# Вывод — по одной строке на файл: «<байты> <путь>» плюс отдельные строки
# «gzip <байты>» для CSS и JS.

set -euo pipefail

frontend_dir="${1:-frontend}"

if [[ ! -d "$frontend_dir" ]]; then
  echo "нет каталога: $frontend_dir" >&2
  exit 1
fi

cd "$frontend_dir"

npm run build >/dev/null

shopt -s nullglob
css_files=(dist/assets/*.css)
js_files=(dist/assets/*.js)
shopt -u nullglob

if [[ ${#css_files[@]} -eq 0 || ${#js_files[@]} -eq 0 ]]; then
  echo "сборка не дала dist/assets/*.css или *.js" >&2
  exit 1
fi

for file in "${css_files[@]}" "${js_files[@]}"; do
  printf '%s %s\n' "$(wc -c < "$file")" "$file"
done

printf 'gzip %s\n' "$(cat "${css_files[@]}" | gzip -c | wc -c)"
printf 'gzip %s\n' "$(cat "${js_files[@]}" | gzip -c | wc -c)"
