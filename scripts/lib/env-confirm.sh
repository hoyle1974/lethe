#!/bin/bash
# Shared helper: select instance config file and confirm before proceeding.
#
# Usage: source scripts/lib/env-confirm.sh
#        require_env_and_confirm "$1" "[extra usage hint]"
#
# With no argument (or empty string): uses .env  (default instance)
# With a name like "staging":         uses .env.staging

require_env_and_confirm() {
  local instance_arg="${1:-}"
  local extra_usage="${2:-}"

  local REPO_ROOT
  REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[1]}")/.." && pwd)"

  if [ -z "$instance_arg" ]; then
    export ENV_TARGET="default"
    export ENV_FILE="$REPO_ROOT/.env"
  else
    export ENV_TARGET="$instance_arg"
    export ENV_FILE="$REPO_ROOT/.env.$instance_arg"
  fi

  echo -e "\033[1;33mInstance: $ENV_TARGET  Config: $ENV_FILE\033[0m"
  read -r -p "Continue? [y/N] " confirm
  case "$confirm" in
    [yY][eE][sS]|[yY]) ;;
    *) echo "Aborted."; exit 1 ;;
  esac
}
