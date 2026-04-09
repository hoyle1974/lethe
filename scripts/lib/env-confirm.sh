#!/bin/bash
# Shared helper: require explicit dev|prod arg, load .env, confirm.
# Usage: source scripts/lib/env-confirm.sh; require_env_and_confirm "$1" "[extra usage]"

require_env_and_confirm() {
  local env_arg="$1"
  local extra_usage="${2:-}"

  if [ "$env_arg" != "dev" ] && [ "$env_arg" != "prod" ]; then
    echo "Usage: $0 <dev|prod>$extra_usage"
    exit 1
  fi

  export ENV_TARGET="$env_arg"

  if [ "$ENV_TARGET" = "prod" ]; then
    export ENV_FILE="$(cd "$(dirname "${BASH_SOURCE[1]}")/.." && pwd)/.env.prod"
  else
    export ENV_FILE="$(cd "$(dirname "${BASH_SOURCE[1]}")/.." && pwd)/.env"
  fi

  echo -e "\033[1;33mEnvironment: $ENV_TARGET  Config: $ENV_FILE\033[0m"
  read -r -p "Continue? [y/N] " confirm
  case "$confirm" in
    [yY][eE][sS]|[yY]) ;;
    *) echo "Aborted."; exit 1 ;;
  esac
}
