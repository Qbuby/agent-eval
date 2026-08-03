#!/usr/bin/env bash
# 在 WSL 里把一个 .sql 文件喂给 agent-eval-postgres 容器执行。
# 用法: bash scripts/psql_q.sh scripts/xxx.sql
# 存在的原因: PowerShell -> wsl -> docker exec -> psql 多层嵌套引号会被逐层吃掉，
# 凭据与 SQL 全部放进脚本内部，调用方只传文件名。
set -euo pipefail

SQL_FILE="${1:?usage: psql_q.sh <sql-file>}"

docker exec -i agent-eval-postgres \
  sh -c 'exec psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -P pager=off -f -' \
  < "$SQL_FILE"
