"""YouTube API キーのローテーション。record_minutely.py から使う。

record_minutely.py 本体はモジュールレベルで即実行されるトップレベルスクリプト
(importするだけで実際にTurso接続・YouTube API呼び出しまで走ってしまう)なので、
テスト容易性のためこのロジックだけ副作用の無い別ファイルに分離している
(comment_velocity.py/hourly_archive.py と同じ構成)。

レート制限(短時間の一時的な制限)とクォータ枯渇(太平洋時間の日次リセットまで
回復しない)を区別する(2026-07-28、comment_sync/sync.py と同じ理由で分離)。
分けずに両方を即座にローテーションすると、単なる一時的な混雑で「このキーは
まだ十分予算が残っているのに次のキーへ切り替える」誤判定を招き、複数
プロジェクトを跨いで運用している意味が薄れる。
"""
import os
import sys
import time

from googleapiclient.errors import HttpError

MAX_RATE_LIMIT_RETRIES = 3  # comment_sync(5回)より少なめ。wait_until_next_minute()で
                              # 1分ちょうどに同期する設計のため、バックオフで待ちすぎると
                              # 次の分のトリガーとキューイングして遅延が蓄積するのを避ける。
RATE_LIMIT_BACKOFF_BASE_SEC = 2  # 2, 4, 8 秒と指数バックオフ(最大14秒/呼び出し)

_API_KEYS = [k for k in [
    os.getenv("YOUTUBE_API_KEY"),
    os.getenv("YOUTUBE_API_KEY2"),
    os.getenv("YOUTUBE_API_KEY3"),
] if k]
_key_idx = 0


def is_daily_quota_error(e: HttpError) -> bool:
    """1日のプロジェクトクォータを使い切った(太平洋時間の日次リセットまで回復しない)。"""
    err = str(e).lower()
    return e.resp.status in (403, 429) and any(s in err for s in [
        "quotaexceeded", "dailylimitexceeded", "userdailylimitexceeded",
    ])


def is_rate_limit_error(e: HttpError) -> bool:
    """短時間(概ね100秒)のレート制限。日次クォータの枯渇ではなく、少し待てば
    同じキーのまま回復する。userRateLimitExceeded/rateLimitExceeded がこれに該当。"""
    err = str(e).lower()
    return e.resp.status in (403, 429) and "ratelimitexceeded" in err


def fetch_with_rotation(fn, *args):
    """レート制限は同じキーのままバックオフして再試行し、日次クォータ枯渇
    (またはバックオフ上限に達したレート制限)は次のキーへローテーションする。
    全キーを使い切ったら今回の記録をスキップして終了する(sys.exit(0))。
    """
    global _key_idx
    for _ in range(len(_API_KEYS)):
        rate_limit_retries = 0
        while True:
            try:
                return fn(_API_KEYS[_key_idx], *args)
            except HttpError as e:
                if is_rate_limit_error(e) and rate_limit_retries < MAX_RATE_LIMIT_RETRIES:
                    wait = RATE_LIMIT_BACKOFF_BASE_SEC * (2 ** rate_limit_retries)
                    rate_limit_retries += 1
                    print(f"レート制限、{wait}秒待って同じキーでリトライ"
                          f"({rate_limit_retries}/{MAX_RATE_LIMIT_RETRIES})")
                    time.sleep(wait)
                    continue
                if is_daily_quota_error(e) or is_rate_limit_error(e):
                    _key_idx = (_key_idx + 1) % len(_API_KEYS)
                    print(f"クォータ枯渇 → キー {_key_idx + 1}/{len(_API_KEYS)} にローテーション")
                    break
                raise
    print("ERROR: 全キーのクォータが枯渇。今回の記録をスキップ")
    sys.exit(0)
