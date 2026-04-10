#!/usr/bin/env bash
# tg_self_reply — tmux 세션 헬퍼 (단순 편의용)
#
# detached tmux 세션으로 데몬을 띄움. 서버에서 터미널 안 띄워두고 돌릴 때 편함.
#
# 사용법:
#   bash tmux.command                  # 세션 시작 (detached)
#   tmux attach -t tgself              # 로그 보기
#   #   detach 는 Ctrl+B 다음 D
#   tmux kill-session -t tgself        # 세션 종료
#   tmux ls                            # 떠 있는 세션 확인
#
# 주의: 첫 실행 (auth) 은 phone/code/2FA 를 interactive 로 입력해야 하므로
# `source .venv/bin/activate && python main.py` 로 직접 띄워서 한 번 통과시킨 후
# (tdlib/ 에 세션 생성 확인) 그 다음부터 이 스크립트로 detached 실행 가능.

set -e

SESSION=tgself
DIR="$(cd "$(dirname "$0")" && pwd)"

if tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "[tmux] session '$SESSION' already exists."
    echo "  attach: tmux attach -t $SESSION"
    echo "  stop:   tmux kill-session -t $SESSION"
    exit 1
fi

tmux new-session -d -s "$SESSION" -c "$DIR" \
    "source .venv/bin/activate && exec python main.py"

echo "[tmux] session '$SESSION' started in $DIR"
echo "  attach: tmux attach -t $SESSION"
echo "  detach: Ctrl+B then D"
echo "  stop:   tmux kill-session -t $SESSION"
