#!/usr/bin/env bash
set -euo pipefail

if [[ "$#" -ne 2 ]]; then
  echo "usage: guardedops_create_user.sh (--create-home|--no-create-home) <username>" >&2
  exit 64
fi

home_mode="$1"
username="$2"

case "$home_mode" in
  --create-home|--no-create-home)
    ;;
  *)
    echo "invalid home mode" >&2
    exit 64
    ;;
esac

if [[ ! "$username" =~ ^[a-z_][a-z0-9_-]{2,31}$ ]]; then
  echo "invalid username" >&2
  exit 65
fi

case "$username" in
  root|admin|administrator|sudo|wheel|daemon|bin|sys|sync|games|man|lp|mail|news|uucp|proxy|www-data|backup|list|irc|gnats|nobody|systemd-network|systemd-resolve|sshd)
    echo "refusing system or privileged username" >&2
    exit 65
    ;;
esac

if getent passwd "$username" >/dev/null; then
  echo "user already exists" >&2
  exit 70
fi

if [[ "$home_mode" == "--create-home" ]]; then
  exec /usr/sbin/useradd --create-home "$username"
fi

exec /usr/sbin/useradd --no-create-home "$username"
