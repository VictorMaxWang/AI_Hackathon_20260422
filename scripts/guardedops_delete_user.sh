#!/usr/bin/env bash
set -euo pipefail

if [[ "$#" -ne 2 ]]; then
  echo "usage: guardedops_delete_user.sh (--keep-home|--remove-home) <username>" >&2
  exit 64
fi

home_mode="$1"
username="$2"

case "$home_mode" in
  --keep-home|--remove-home)
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

passwd_record="$(getent passwd "$username" || true)"
if [[ -z "$passwd_record" ]]; then
  echo "user does not exist" >&2
  exit 70
fi

IFS=: read -r record_username _ uid _ _ _ _ <<< "$passwd_record"
if [[ "$record_username" != "$username" ]]; then
  echo "passwd record mismatch" >&2
  exit 70
fi

if [[ ! "$uid" =~ ^[0-9]+$ ]]; then
  echo "invalid uid" >&2
  exit 70
fi

if (( uid < 1000 )); then
  echo "refusing to delete system user" >&2
  exit 71
fi

current_user="$(id -un)"
if [[ "$current_user" == "$username" ]]; then
  echo "refusing to delete current user" >&2
  exit 71
fi

if [[ "$home_mode" == "--remove-home" ]]; then
  exec /usr/sbin/userdel --remove "$username"
fi

exec /usr/sbin/userdel "$username"
