#!/bin/bash
# clean.sh —— 清理产物，只保留原始结构文件

set -e
cd "$(dirname "$0")"

echo "=== 保留: struct/ ==="
echo "  (不删除 struct/ 下任何文件)"

echo ""
echo "=== 清理旧目录 (topo/ process/ model.pdb) ==="
rm -rf topo/ process/
rm -f model.pdb model.inp model.pdb_FORCED
echo "  已删除"

echo ""
echo "=== 清空 md_run/ ==="
rm -rf md_run/*
echo "  md_run/ 已清空"

echo ""
echo "=== 清理根目录中间文件 ==="
rm -f em.mdp eq.mdp prod.mdp
rm -f xx.inp test.chk
rm -f .pipeline.lock .md_counter
rm -rf __pycache__
echo "  已清理"

echo ""
echo "=== 完成 === 保留: struct/ | 清空: md_run/"
