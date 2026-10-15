#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/.." && pwd)"
source_dir="${repo_root}/skill/hell-grind-aigc-skill"

if [[ -n "${CODEX_HOME:-}" ]]; then
  skills_root="${CODEX_HOME}/skills"
else
  skills_root="${HOME}/.codex/skills"
fi

target_dir="${skills_root}/hell-grind-aigc-skill"
update_mode="false"

if [[ "${1:-}" == "--update" ]]; then
  update_mode="true"
elif [[ $# -gt 0 ]]; then
  echo "Usage: $0 [--update]" >&2
  exit 2
fi

if [[ ! -d "${source_dir}" ]]; then
  echo "Skill source not found: ${source_dir}" >&2
  exit 2
fi

mkdir -p "${skills_root}"

backup_dir=""
if [[ -e "${target_dir}" ]]; then
  if [[ "${update_mode}" != "true" ]]; then
    echo "Target already exists: ${target_dir}" >&2
    echo "Run with --update to back up and replace it." >&2
    exit 2
  fi
  backup_root="${skills_root}/.backups"
  mkdir -p "${backup_root}"
  backup_dir="${backup_root}/hell-grind-aigc-skill-$(date '+%Y%m%d-%H%M%S')"
  mv "${target_dir}" "${backup_dir}"
fi

if ! cp -R "${source_dir}" "${target_dir}"; then
  if [[ -n "${backup_dir}" && ! -e "${target_dir}" ]]; then
    mv "${backup_dir}" "${target_dir}"
  fi
  echo "Installation failed." >&2
  exit 1
fi

echo "Installed: ${target_dir}"
if [[ -n "${backup_dir}" ]]; then
  echo "Backup: ${backup_dir}"
fi
